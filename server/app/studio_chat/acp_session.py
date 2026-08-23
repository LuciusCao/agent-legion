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
from typing import Any, Protocol, cast

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import (
    AllowedOutcome as AcpAllowedOutcome,
)
from acp.schema import (
    ClientCapabilities,
    HttpHeader,
    HttpMcpServer,
    Implementation,
    RequestPermissionResponse,
    TextContentBlock,
)
from acp.schema import (
    DeniedOutcome as AcpDeniedOutcome,
)

from server.app.mcp_server.config import SESSION_ID_HEADER
from server.app.mcp_server.http_app import MCP_URL_PATH
from server.app.studio_chat.capabilities import capability_snapshot

logger = logging.getLogger(__name__)

_CLOSE = object()


def _log_cancel_result(task: asyncio.Task[Any]) -> None:
    """Retrieve the cancel task's result so a failure never surfaces as an
    unretrieved-exception warning on an otherwise healthy loop."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("studio chat ACP cancel failed: %s", exc)


# Safety net for a wedged agent turn; cancel() is the intended control path.
PROMPT_TIMEOUT_SECONDS = 3600
# Grace for the loop to drain _CLOSE and let the SDK transport shut the child
# down (stdin EOF -> terminate) before close() escalates to kill.
CLOSE_GRACE_SECONDS = 5


class AcpSessionCallbacks(Protocol):
    """Service-side hooks invoked from the session thread; must be thread-safe."""

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

    def on_exit(self) -> None: ...


class _ClientImpl:
    """ACP client surface the agent calls back into (duck-typed protocol)."""

    def __init__(self, handle: AcpSessionHandle) -> None:
        self._handle = handle

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
    ) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.mcp_server = mcp_server
        self.env = dict(env or {})
        self.callbacks = callbacks
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
        with contextlib.suppress(Exception):
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
            loop, process = self._loop, self._process
        # Re-validate identity and liveness right before the cross-process
        # action: only kill the child this handle spawned, only while alive.
        if loop is None or process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(process.kill)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            logger.exception("studio chat ACP session loop crashed")
            self.callbacks.on_error("ACP session loop crashed")
        finally:
            self.ready_event.set()
            self.callbacks.on_exit()

    async def _run(self) -> None:
        client = _ClientImpl(self)
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
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(
                        name="agent-legion-studio", title="Agent Legion", version="1"
                    ),
                )
                capabilities = capability_snapshot(initialize)
                session = await conn.new_session(cwd=self.cwd, mcp_servers=[self.mcp_server])
                with self._state_lock:
                    self._acp_session_id = session.session_id
                self.callbacks.on_ready(capabilities, session.session_id)
                # Startup handshake complete: release the create_session waiter.
                self.ready_event.set()
                await self._prompt_loop(conn, session.session_id)
        except Exception as exc:
            logger.warning("studio chat ACP session failed: %s", exc)
            self.callbacks.on_error(str(exc))

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
                self.callbacks.on_turn_error(str(exc))

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


def build_mcp_server_spec(*, token: str, api_base: str, session_id: str) -> HttpMcpServer:
    """The session-scoped agent-legion MCP entry injected into session/new.

    kimi ≥ 0.38 only accepts http/sse MCP servers over ACP, so the backend
    serves the tool surface itself (server.app.mcp_server.http_app) and the
    session points at that URL. The raw scoped token crosses only as an HTTP
    header inside the ACP session/new request — never persisted, never logged
    (STUDIO-AGENT-001). The chat session id rides along (SESSION_ID_HEADER)
    so the get_studio_context tool can resolve this session's live context.
    """
    return HttpMcpServer(
        type="http",
        name="agent-legion-studio",
        url=f"{api_base}{MCP_URL_PATH}",
        headers=[
            HttpHeader(name="Authorization", value=f"Bearer {token}"),
            HttpHeader(name=SESSION_ID_HEADER, value=session_id),
        ],
    )
