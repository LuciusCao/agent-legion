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
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import (
    AllowedOutcome as AcpAllowedOutcome,
)
from acp.schema import (
    ClientCapabilities,
    HttpMcpServer,
    Implementation,
    RequestPermissionResponse,
    TextContentBlock,
)
from acp.schema import (
    DeniedOutcome as AcpDeniedOutcome,
)

from server.app.studio_chat.capabilities import capability_snapshot
from server.app.studio_chat.session_load import open_acp_session
from server.app.studio_chat.terminals import AcpTerminalStore, TerminalClientMixin

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

_CLOSE = object()


def _log_cancel_result(task: asyncio.Task[Any]) -> None:
    """Retrieve the cancel task's result so a failure never surfaces as an
    unretrieved-exception warning on an otherwise healthy loop."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.warning("studio chat ACP cancel failed: %s", exc)


# Safety net for a wedged agent turn; cancel() is the intended control path.
PROMPT_TIMEOUT_SECONDS = 3600
# Grace for the loop to drain _CLOSE and let the SDK transport shut the child
# down (stdin EOF -> terminate) before close() escalates to kill.
CLOSE_GRACE_SECONDS = 5


class AcpSessionCallbacks(Protocol):
    """Service-side hooks invoked from the session thread; must be thread-safe."""

    # Bound by spawn_session_runtime right after the runtime is created
    # (before handle.start()): the death-echo on_exit pins this identity so a
    # stale exit cannot tear down a newer runtime registered by resume (ABA).
    runtime: SessionRuntime | None

    def on_ready(self, capabilities: dict[str, Any], acp_session_id: str) -> None: ...

    def on_update(self, update: dict[str, Any]) -> None: ...

    def on_permission_request(
        self, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Block until a decision: {"option_id": ...} or {"deny": True}."""

    def on_turn_end(self, stop_reason: str) -> None: ...

    def on_turn_error(self, detail: str) -> None:
        """A single prompt turn failed; the session loop keeps running."""

    def on_error(self, detail: str) -> None:
        """The whole ACP run collapsed (startup failure or connection loss)."""

    def on_exit(self, *, close_initiated: bool) -> None: ...


class _ClientImpl(TerminalClientMixin):
    """ACP client surface the agent calls back into (duck-typed protocol);
    ``_handle``/``terminals`` are bound by the factory in ``_run``."""

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        payload = update.model_dump(by_alias=True, exclude_none=True, mode="json")
        self._handle.callbacks.on_update(payload)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
    ) -> RequestPermissionResponse:
        tool_call_payload = tool_call.model_dump(by_alias=True, exclude_none=True, mode="json")
        option_payloads = [
            option.model_dump(by_alias=True, exclude_none=True, mode="json") for option in options
        ]
        decision = await asyncio.to_thread(
            self._handle.callbacks.on_permission_request, tool_call_payload, option_payloads
        )
        option_id = decision.get("option_id")
        if option_id:
            return RequestPermissionResponse(
                outcome=AcpAllowedOutcome(outcome="selected", option_id=option_id)
            )
        return RequestPermissionResponse(outcome=AcpDeniedOutcome(outcome="cancelled"))


class AcpSessionHandle:
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
    ) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.mcp_server = mcp_server
        self.env = dict(env or {})
        self.callbacks = callbacks
        # Resume path: try session/load of this prior ACP session when the
        # freshly-initialized agent advertises loadSession (session_load.py).
        self._resume_acp_session_id = resume_acp_session_id
        # Set once startup completed: True only when session/load actually
        # restored the prior ACP session (False on the session/new fallback).
        self.loaded_existing = False
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state_lock = threading.Lock()
        # Loop-owned handles, captured under _state_lock for cross-thread
        # cancel/kill; None until the connection is up.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: Any = None
        self._process: Any = None
        self._acp_session_id: str | None = None
        self.ready_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name="studio-chat-acp", daemon=True
        )
        self._thread.start()

    def send_prompt(self, text: str) -> bool:
        """Queue a prompt turn; False when the handle is already closed."""
        with self._state_lock:
            if self._closed:
                return False
            self._queue.put(text)
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
            logger.exception("studio chat ACP session loop crashed")
            self.callbacks.on_error("ACP session loop crashed")
        finally:
            self.ready_event.set()
            # _closed was set before _CLOSE was queued, so by the time the
            # thread drains it and lands here the flag is reliably visible:
            # an intentional teardown must not be reported as an agent death.
            self.callbacks.on_exit(close_initiated=self._closed)

    async def _run(self) -> None:
        client = _ClientImpl()
        client._handle, client.terminals = self, AcpTerminalStore()
        try:
            async with spawn_agent_process(
                cast(Any, client), self.command, *self.args, env=self.env, cwd=self.cwd
            ) as (conn, process):
                with self._state_lock:
                    self._loop = asyncio.get_running_loop()
                    self._conn = conn
                    self._process = process
                self._drain_stderr(process)
                initialize = await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    # terminal=True: kimi's Bash/Grep run via the ACP
                    # terminal protocol; without the flag they fail upfront.
                    client_capabilities=ClientCapabilities(terminal=True),
                    client_info=Implementation(
                        name="agent-legion-studio", title="Agent Legion", version="1"
                    ),
                )
                capabilities = capability_snapshot(initialize)
                acp_session_id, loaded = await open_acp_session(
                    conn,
                    cwd=self.cwd,
                    mcp_server=self.mcp_server,
                    resume_acp_session_id=self._resume_acp_session_id,
                    capabilities=capabilities,
                )
                with self._state_lock:
                    self._acp_session_id = acp_session_id
                self.loaded_existing = loaded
                self.callbacks.on_ready(capabilities, acp_session_id)
                # Startup handshake complete: release the create_session waiter.
                self.ready_event.set()
                await self._prompt_loop(conn, acp_session_id)
        except Exception as exc:
            # exc_info: this is the primary failure signal for the whole ACP
            # session lifecycle — losing the traceback makes spawn/transport
            # problems undiagnosable from the logs alone.
            # #204 broad-except audit: the catch stays broad because the
            # outcome space is genuinely mixed — expected agent-side
            # refusals (RequestError from initialize / session open),
            # transport deaths (ConnectionError / OSError) and programming
            # errors all funnel into the SAME designed semantics here: the
            # session is marked error and the user sees a dead session
            # instead of a hung one. Nothing is masked (the traceback is
            # logged) and no exception type is converted on the way out.
            logger.warning("studio chat ACP session failed: %s", exc, exc_info=True)
            self.callbacks.on_error(str(exc))
        finally:
            # Reap terminals a crashed/killed agent never released itself.
            # #204 suppress audit: close_all already contains per-terminal
            # failure isolation (its _swallow helper logs each kill/release),
            # so this suppress only guards the finally itself — an exception
            # raised while reaping must not REPLACE the failure already
            # propagating out of _run (Python would otherwise drop the
            # original). Cleanup-never-classify, the #233 pattern.
            with contextlib.suppress(Exception):
                await client.terminals.close_all()

    async def _prompt_loop(self, conn: Any, acp_session_id: str) -> None:
        while True:
            item = await asyncio.to_thread(self._queue.get)
            if item is _CLOSE:
                return
            try:
                response = await asyncio.wait_for(
                    conn.prompt(acp_session_id, [TextContentBlock(type="text", text=str(item))]),
                    timeout=PROMPT_TIMEOUT_SECONDS,
                )
                self.callbacks.on_turn_end(str(response.stop_reason))
            except Exception as exc:
                # #204 broad-except audit: deliberate per-turn containment —
                # the prompt loop is the session's life support, so one failed
                # turn must not kill the loop and the session with it; the
                # service records the turn error and the user can send the
                # next prompt, and the traceback is logged for the agent-side
                # failures that dominate here.
                self.callbacks.on_turn_error(f"{type(exc).__name__}: {exc}")
                logger.warning("studio chat prompt turn failed: %s", exc, exc_info=True)

    def _drain_stderr(self, process: Any) -> None:
        """Discard agent stderr on a reader task so a chatty agent never
        deadlocks on a full pipe; lines go to the debug log only."""
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
