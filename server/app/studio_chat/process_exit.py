"""Event-driven direct-child and ACP receive-stream death detection (#1153).

ACP 0.12 rejects outstanding requests when its receive loop ends, but has no
public disconnect callback. Its receive task is therefore observed alongside
``process.wait()``; neither waiter reads or consumes the stdout byte stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class AcpConnectionLost(RuntimeError):
    """The current handle's direct child or ACP receive stream died."""


def _receive_task(conn: Any) -> asyncio.Task[Any] | None:
    # ACP 0.12.0: ClientSideConnection._conn is Connection, whose _recv_task
    # ends on stdout EOF and rejects outgoing futures. This observes only the
    # task, never its StreamReader, so protocol framing remains SDK-owned.
    task = getattr(getattr(conn, "_conn", None), "_recv_task", None)
    return task if isinstance(task, asyncio.Task) else None


async def watch_connection(
    conn: Any,
    process: Any,
    *,
    close_initiated: Callable[[], bool],
    note_death: Callable[[str], bool],
) -> str | None:
    """Return the first death, or None for an explicit handle close."""
    process_wait = (
        asyncio.create_task(process.wait()) if callable(getattr(process, "wait", None)) else None
    )
    receive_task = _receive_task(conn)
    waiters = {task for task in (process_wait, receive_task) if task is not None}
    if not waiters:
        await asyncio.Future()
    done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    if process_wait is not None and process_wait not in done:
        process_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_wait
    if close_initiated():
        return None
    if receive_task in done:
        detail = "ACP receive stream closed"
    else:
        assert process_wait is not None
        detail = f"agent process exited (returncode={process_wait.result()})"
    # Re-check close under the handle's own state lock: close winning between
    # the observation and this note keeps explicit-close semantics.
    return detail if note_death(detail) else None


async def race_with_death(awaitable: Awaitable[Any], death: asyncio.Task[str | None]) -> Any:
    """Await startup work without letting a dead child wait for the RPC timeout."""
    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task, death}, return_when=asyncio.FIRST_COMPLETED)
    if death not in done:
        return task.result()
    detail = death.result()
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if detail is not None:
        raise AcpConnectionLost(detail)
    return None


async def report_connection_loss(callbacks: Any, detail: str) -> None:
    """Fence and project before transport teardown; the thread-final on_exit
    remains another idempotent retry if both immediate attempts fail."""
    logger.warning("studio chat ACP connection lost: %s", detail)
    for attempt in range(2):
        try:
            await asyncio.to_thread(callbacks.on_connection_lost, detail)
            return
        except Exception:
            # #204 broad-except audit: projection includes DB and snapshot I/O.
            # Cleanup has its own finally fence; retry the same generation and
            # preserve every traceback instead of converting this to turn error.
            logger.warning(
                "studio chat exit projection attempt %d failed", attempt + 1, exc_info=True
            )
