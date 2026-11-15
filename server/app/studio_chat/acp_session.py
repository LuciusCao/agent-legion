"""Per-session ACP agent subprocess handle (phase 3 chunk 4).

Each Studio chat session owns one ACP agent subprocess spoken to over stdio
JSON-RPC via the ``acp`` SDK. The SDK is asyncio-only while the FastAPI app
and the service layer are thread-based, so every handle runs its own event
loop on a dedicated daemon thread (the WorkflowWorkerThread lifecycle
pattern). Prompts travel to the loop through a thread-safe queue; cancel and
kill cross via ``loop.call_soon_threadsafe`` because a cancel must reach the
agent while a prompt turn is still in flight. All agent-originated traffic
(session/update notifications, permission requests) leaves the loop through
the ``AcpSessionCallbacks`` hooks, which the service implements thread-safely.

Lifecycle: start() -> on_ready (capabilities + acp session id) -> prompt
turns (send_prompt / cancel) -> close(). close() re-validates the handle and
process identity before escalating to kill (graceful queue drain first, then
``process.kill()`` on the still-live child we spawned), and is idempotent so
a double close never kills a recycled handle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from acp import spawn_agent_process
from acp.schema import HttpMcpServer

from server.app.studio_chat.acp_consumer import (
    _CLOSE,
    drain_stderr,
    next_item,
    prompt_loop,
    run_acp_session,
)
from server.app.studio_chat.acp_session_config import SessionConfigHandleMixin
from server.app.studio_chat.prompt_turn import run_prompt_turn
from server.app.studio_chat.session_load import open_acp_session

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.session_config_state import OpenedAcpSession

logger = logging.getLogger(__name__)


def _log_cancel_result(task: asyncio.Task[Any]) -> None:
    """Retrieve the cancel task's result so a failure never surfaces as an
    unretrieved-exception warning on an otherwise healthy loop."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.warning("studio chat ACP cancel failed: %s", exc)


# Grace for the loop to drain _CLOSE and let the SDK transport shut the child
# down (stdin EOF -> terminate) before close() escalates to kill.
CLOSE_GRACE_SECONDS = 5


class AcpSessionCallbacks(Protocol):
    """Service-side hooks invoked from the session thread; must be thread-safe."""

    # Bound by spawn_session_runtime right after the runtime is created
    # (before handle.start()): the death-echo on_exit pins this identity so a
    # stale exit cannot tear down a newer runtime registered by resume (ABA).
    runtime: SessionRuntime | None

    def on_ready(self, capabilities: dict[str, Any], opened: OpenedAcpSession) -> None: ...

    def on_update(self, update: dict[str, Any]) -> None: ...

    def on_permission_request(
        self, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Block until a decision: {"option_id": ...} or {"deny": True}."""

    def on_turn_end(self, stop_reason: str, *, timed_out: bool = False) -> None: ...

    def on_turn_timeout(self) -> None:
        """The turn hit the prompt timeout; the ladder auto-cancels next.
        Settle parked permissions as denied so an agent parked on a
        permission response can still end the turn (#664 review)."""

    def on_turn_error(self, detail: str) -> None:
        """A single prompt turn failed; the session loop keeps running."""

    def on_error(self, detail: str) -> None:
        """The whole ACP run collapsed (startup failure or connection loss)."""

    def on_connection_lost(self, detail: str) -> None:
        """Fence/project before transport cleanup; final on_exit owns removal."""

    def on_exit(self, *, close_initiated: bool) -> None: ...


class AcpSessionHandle(SessionConfigHandleMixin):
    """Owns one ACP agent subprocess plus its asyncio loop thread."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        cwd: str,
        mcp_server: HttpMcpServer,
        env: Mapping[str, str] | None,
        callbacks: AcpSessionCallbacks,
        resume_acp_session_id: str | None = None,
        terminal_grant_required: Callable[[], bool] | None = None,
    ) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.mcp_server = mcp_server
        self.env = dict(env or {})
        self.callbacks = callbacks
        # #1136: the #921 grant fence is opt-in (engine in auto permission
        # mode never sends request_permission, so no grant can ever be minted
        # and Bash is dead). None = fence off (pre-#921 direct execution).
        self.terminal_grant_required = terminal_grant_required or (lambda: False)
        # Resume path: try session/load of this prior ACP session when the
        # freshly-initialized agent advertises loadSession (session_load.py).
        self._resume_acp_session_id = resume_acp_session_id
        # Set once startup completed: True only when session/load actually
        # restored the prior ACP session (False on the session/new fallback).
        self.loaded_existing = False
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False
        # request_stop() only asks for a graceful post-turn exit; unlike
        # close() it must NOT flip _closed, or a later close() (resume's
        # winner-side teardown) would return at its idempotence gate without
        # joining or killing a turn that is still running (codex P1).
        self._stop_requested = False
        self._state_lock = threading.Lock()
        # Loop-owned handles, captured under _state_lock for cross-thread
        # cancel/kill; None until the connection is up.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: Any = None
        self._process: Any = None
        self._transport_failure: str | None = None
        self._acp_session_id: str | None = None
        self.ready_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name="studio-chat-acp", daemon=True
        )
        self._thread.start()

    def transport_failure(self) -> str | None:
        with self._state_lock:
            return self._transport_failure

    def _note_connection_lost(self, detail: str) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            self._transport_failure = detail
            self._stop_requested = True
            return True

    def send_prompt(
        self,
        text: str,
        *,
        before_start: Callable[[], bool] | None = None,
        accept: Callable[[], None] | None = None,
    ) -> bool:
        """Accept durable input only while the queue can still receive it.

        The callback commits the turn and message together, or raises with
        neither committed. Close/stop cannot interleave before queue.put;
        the unbounded queue is the final non-blocking handoff.
        """
        with self._state_lock:
            if self._closed or self._stop_requested:
                return False
            if accept is not None:
                accept()
            self._queue.put((text, before_start) if before_start is not None else text)
            return True

    def cancel(self) -> None:
        """Send session/cancel even while a prompt turn is in flight."""
        with self._state_lock:
            if self._closed:
                return
            loop, conn, acp_session_id = self._loop, self._conn, self._acp_session_id
        if loop is None or conn is None or acp_session_id is None:
            return

        def _send() -> None:
            task = asyncio.create_task(conn.cancel(acp_session_id))
            task.add_done_callback(_log_cancel_result)

        # The loop may already be closed (session torn down between the state
        # check and the hand-off); align with _kill_process and never let a
        # late cancel surface as a 500.
        # #204 suppress audit: call_soon_threadsafe fails with exactly two
        # families in that race — RuntimeError from the loop's closed-check
        # and OSError when the self-pipe socket closes between the check and
        # the write. Narrowing to that pair keeps the teardown race silent
        # while a genuine programming error in the hand-off now surfaces
        # instead of being swallowed as a no-op cancel.
        with contextlib.suppress(RuntimeError, OSError):
            loop.call_soon_threadsafe(_send)

    def close(self) -> None:
        """Stop the loop and reap the subprocess; safe to call more than once."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(_CLOSE)
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=CLOSE_GRACE_SECONDS)
        if thread.is_alive():
            self._kill_process()
            thread.join(timeout=CLOSE_GRACE_SECONDS * 2)
            if thread.is_alive():
                logger.warning("studio chat ACP session thread did not stop in time")

    def request_stop(self) -> None:
        """Ask the session to stop after the current turn; never blocks.

        #558: the dead-token escalation wants the (healthy) ACP process gone
        once the running turn finishes, without killing it mid-turn and
        without deadlocking on the ACP thread — keepalive runs ON that
        thread, so close()'s join() is a self-join. The prompt loop drains
        the queue only between turns, so a _CLOSE enqueued now lands exactly
        after the current turn: the loop returns, the async-with tears the
        subprocess down gracefully, and the existing on_exit path finishes
        the cleanup (registry pop, token revoke).

        Deliberately does NOT set ``_closed`` (codex P1): a resume racing in
        mid-turn still needs close()'s full join→kill teardown of THIS
        runtime before the new one spawns. The extra _CLOSE it queues is
        harmless — the loop is already exiting. A wedged turn bounds the
        graceful wait at PROMPT_TIMEOUT_SECONDS + CANCEL_GRACE_SECONDS: the
        #664 timeout ladder (prompt_turn.py) sends session/cancel and, when
        the agent never acknowledges it, escalates to the fatal path, which
        fails the session and tears the subprocess down without draining the
        queued _CLOSE; a parked permission at the 120s auto-deny."""
        with self._state_lock:
            if self._closed or self._stop_requested:
                return
            self._stop_requested = True
            self._queue.put(_CLOSE)

    def _kill_process(self) -> None:
        with self._state_lock:
            process = self._process
        # Re-validate identity and liveness right before the cross-process
        # action: only kill the child this handle spawned, only while alive.
        if process is None or process.returncode is not None:
            return
        # Kill synchronously (#158): routing process.kill through
        # loop.call_soon_threadsafe never fires when the loop itself is
        # wedged — exactly the case this escalation exists for — and the child
        # would leak as an orphan. Process.kill() is a plain signal send and
        # safe to issue off-loop.
        # #204 suppress audit: the re-validated kill still races the child's
        # own death — os.kill then raises ProcessLookupError (pid already
        # reaped) or PermissionError (pid recycled outside our uid); those
        # are the expected teardown races and stay suppressed, mirroring
        # CodeExecutor._terminate_child. Anything else is a real bug in the
        # escalation path and now propagates instead of silently skipping it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            process.kill()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            # #204 broad-except audit: the session thread's last-chance
            # reporter. _run catches its own Exception space, so what lands
            # here is a failure of the finally path (the on_exit callback) or
            # exotic asyncio teardown errors; without this catch the thread
            # would die silently — ready_event never set (spawn then waits
            # out the full 60s timeout) and no on_error reaches the service —
            # so the crash is logged with the traceback and reported to the
            # session as an error instead.
            self.request_stop()
            logger.exception("studio chat ACP session loop crashed")
            self.callbacks.on_error("ACP session loop crashed")
        finally:
            self.request_stop()
            self.ready_event.set()
            # _closed was set before _CLOSE was queued, so by the time the
            # thread drains it and lands here the flag is reliably visible:
            # an intentional teardown must not be reported as an agent death.
            self.callbacks.on_exit(close_initiated=self._closed)

    async def _run(self) -> None:
        await run_acp_session(self, spawn_agent_process, open_acp_session)

    async def _next_item(self, death: asyncio.Task[str | None] | None) -> Any:
        return await next_item(self, death)

    async def _prompt_loop(
        self,
        conn: Any,
        acp_session_id: str,
        death: asyncio.Task[str | None] | None = None,
    ) -> None:
        await prompt_loop(self, conn, acp_session_id, death, run_prompt_turn)

    def _drain_stderr(self, process: Any) -> None:
        drain_stderr(process)
