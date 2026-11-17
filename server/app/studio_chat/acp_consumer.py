"""Asyncio ACP startup and prompt consumer for one session handle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, cast

from acp import PROTOCOL_VERSION
from acp.schema import Implementation

from server.app.studio_chat.acp_client import AcpClient
from server.app.studio_chat.acp_session_config import studio_client_capabilities
from server.app.studio_chat.capabilities import capability_snapshot
from server.app.studio_chat.normal_terminal import prompt_can_finish_item
from server.app.studio_chat.process_exit import (
    AcpConnectionLost,
    race_with_death,
    report_connection_loss,
    watch_connection,
)
from server.app.studio_chat.prompt_admission import QueuedPrompt, prompt_admission_open
from server.app.studio_chat.prompt_turn import PromptWedgedError
from server.app.studio_chat.terminals import AcpTerminalStore
from server.app.studio_chat.turn_watchdog import note_prompt_response, prompt_fatal

if TYPE_CHECKING:
    from server.app.studio_chat.acp_session import AcpSessionHandle

logger = logging.getLogger(__name__)
_CLOSE = object()


async def run_acp_session(
    handle: AcpSessionHandle, spawn_agent_process: Any, open_acp_session: Any
) -> None:
    client = AcpClient()
    client._handle, client.terminals = handle, AcpTerminalStore()
    try:
        async with spawn_agent_process(
            cast(Any, client), handle.command, *handle.args, env=handle.env, cwd=handle.cwd
        ) as (conn, process):
            with handle._state_lock:
                handle._loop = asyncio.get_running_loop()
                handle._conn = conn
                handle._process = process
            drain_stderr(process)
            death = asyncio.create_task(
                watch_connection(
                    conn,
                    process,
                    close_initiated=lambda: handle._closed,
                    note_death=handle._note_connection_lost,
                )
            )
            try:
                initialize = await race_with_death(
                    conn.initialize(
                        protocol_version=PROTOCOL_VERSION,
                        client_capabilities=studio_client_capabilities(),
                        client_info=Implementation(
                            name="agent-legion-studio", title="Agent Legion", version="1"
                        ),
                    ),
                    death,
                )
                if initialize is None:
                    return
                capabilities = capability_snapshot(initialize)
                opened = await race_with_death(
                    open_acp_session(
                        conn,
                        cwd=handle.cwd,
                        mcp_server=handle.mcp_server,
                        resume_acp_session_id=handle._resume_acp_session_id,
                        capabilities=capabilities,
                    ),
                    death,
                )
                if opened is None:
                    return
                acp_session_id = opened.acp_session_id
                with handle._state_lock:
                    handle._acp_session_id = acp_session_id
                handle.loaded_existing = opened.loaded_existing
                handle.callbacks.on_ready(capabilities, opened)
                handle.ready_event.set()
                await handle._prompt_loop(conn, acp_session_id, death)
            except AcpConnectionLost as exc:
                # Projection fences this runtime before the transport's async
                # context starts its bounded terminate/kill cleanup.
                await report_connection_loss(handle.callbacks, str(exc))
            finally:
                handle.request_stop()
                death.cancel()
                await asyncio.gather(death, return_exceptions=True)
    except Exception as exc:
        # #204 broad-except audit: startup RequestError, transport death,
        # PromptWedgedError and programming failures share the designed fatal
        # session path; the original traceback is logged, never classified away.
        handle.request_stop()
        logger.warning("studio chat ACP session failed: %s", exc, exc_info=True)
        handle.callbacks.on_error(str(exc))
    finally:
        # Reap only terminals registered through this direct ACP child. Kimi's
        # unrelated/non-direct background tasks are not process-group targets.
        with contextlib.suppress(Exception):
            await client.terminals.close_all()


async def next_item(handle: AcpSessionHandle, death: asyncio.Task[str | None] | None) -> Any:
    if death is None:
        return await asyncio.to_thread(handle._queue.get)
    queued = asyncio.create_task(asyncio.to_thread(handle._queue.get))
    done, _pending = await asyncio.wait({queued, death}, return_when=asyncio.FIRST_COMPLETED)
    if death not in done:
        return queued.result()
    # Release the worker-side queue.get without a timed join. Any item it
    # consumed concurrently stays represented by runtime inbound state.
    handle._queue.put(_CLOSE)
    await asyncio.gather(queued, return_exceptions=True)
    if (detail := death.result()) is not None:
        raise AcpConnectionLost(detail)
    return _CLOSE


async def prompt_loop(
    handle: AcpSessionHandle,
    conn: Any,
    acp_session_id: str,
    death: asyncio.Task[str | None] | None,
    prompt_runner: Any,
) -> None:
    while True:
        item = await next_item(handle, death)
        if item is _CLOSE:
            return
        admission_open = prompt_admission_open(handle)
        if not admission_open and not prompt_can_finish_item(item):
            continue
        try:
            if isinstance(item, QueuedPrompt):
                text, lease, before_start = item.text, item.lease, item.before_start
                if admission_open and lease is not None and not lease.can_start():
                    continue
            else:
                text, before_start = item if isinstance(item, tuple) else (str(item), None)
                lease = None
            result = await prompt_runner(
                conn,
                acp_session_id,
                text,
                on_timeout=handle.callbacks.on_turn_timeout,
                death_task=death,
                start_gate=handle._prompt_start_gate,
                lease=lease,
                timeout_ladder=(handle.prompt_timeout_seconds, handle.cancel_grace_seconds),
                **({"before_start": before_start} if before_start is not None else {}),
            )
            note_prompt_response(handle, lease)
            if prompt_fatal(handle, lease):
                if (failure := handle.transport_failure()) is not None:
                    raise AcpConnectionLost(failure)
                continue
            if result.response is None:
                continue
            handle.callbacks.on_turn_end(
                str(result.response.stop_reason), timed_out=result.timed_out
            )
        except AcpConnectionLost:
            raise
        except PromptWedgedError:
            raise
        except (ConnectionError, OSError) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if not handle._note_connection_lost(detail):
                return
            raise AcpConnectionLost(detail) from exc
        except Exception as exc:
            # #204 broad-except audit: only ordinary turn failures (in
            # particular agent RequestError) are contained here. Transport
            # families and the two fatal signals above never return idle.
            note_prompt_response(handle, lease)
            if prompt_fatal(handle, lease):
                logger.warning("studio prompt failed after its fatal fence: %s", exc, exc_info=True)
                if (failure := handle.transport_failure()) is not None:
                    raise AcpConnectionLost(failure) from exc
                continue
            handle.callbacks.on_turn_error(f"{type(exc).__name__}: {exc}")
            logger.warning("studio chat prompt turn failed: %s", exc, exc_info=True)


def drain_stderr(process: Any) -> None:
    """Discard agent stderr so a chatty agent cannot deadlock on a full pipe."""
    stderr = process.stderr
    if stderr is None:
        return

    async def drain() -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            logger.debug("studio chat agent stderr: %s", line.decode(errors="replace").rstrip())

    asyncio.get_running_loop().create_task(drain())
