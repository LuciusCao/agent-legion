"""ACP-thread side of the session config surface (#368).

Split from acp_session.py (file budget): the initialize capability
declaration and the cross-thread set_mode / set_config_option calls.
``SessionConfigHandleMixin`` is mixed into ``AcpSessionHandle`` (duck-typed
over its ``_state_lock`` / ``_conn`` / ``_loop`` internals, the
TerminalClientMixin pattern).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from acp.schema import (
    ClientCapabilities,
    ClientSessionCapabilities,
    SessionConfigOptionsCapabilities,
)

# session/set_mode and session/set_config_option are single JSON-RPC
# round-trips; a healthy agent answers in milliseconds, so a short bound
# keeps a wedged agent from parking the FastAPI worker thread.
SET_CONFIG_TIMEOUT_SECONDS = 30


def studio_client_capabilities() -> ClientCapabilities:
    """The initialize declaration for studio chat sessions.

    terminal=True: kimi's Bash/Grep run via the ACP terminal protocol;
    without the flag they fail upfront. session.configOptions: an empty
    capability object advertises select-type config option support; boolean
    is deliberately NOT declared (no UI for it yet — a half support would
    invite boolean entries we cannot render).
    """
    return ClientCapabilities(
        terminal=True,
        session=ClientSessionCapabilities(config_options=SessionConfigOptionsCapabilities()),
    )


class SessionConfigHandleMixin:
    """set_mode / set_config_option round-trips for AcpSessionHandle.

    Both run on a FastAPI worker thread: the coroutine is handed to the
    session loop and awaited synchronously with a bounded timeout. Raises on
    agent refusal (RequestError), timeout, or a torn-down session.
    """

    # Bound by AcpSessionHandle.__init__ / _run (duck-typed host contract,
    # the TerminalClientMixin pattern).
    _state_lock: threading.Lock
    _closed: bool
    _conn: Any
    _acp_session_id: str | None
    _loop: asyncio.AbstractEventLoop | None

    def set_session_mode(self, mode_id: str) -> None:
        """Send session/set_mode and wait for the agent's answer."""
        conn, acp_session_id = self._live_conn()
        self._await_on_loop(conn.set_session_mode(acp_session_id, mode_id))

    def set_config_option(self, config_id: str, value: str) -> list[dict[str, Any]]:
        """Send session/set_config_option; returns the response's FULL config
        state (the protocol returns every option — a model switch can shift
        the supported thought levels). configOptions is a required response
        field: an agent answering without it fails SDK validation, which
        surfaces to the caller as the same rejection as an agent error."""
        conn, acp_session_id = self._live_conn()
        response = self._await_on_loop(conn.set_config_option(config_id, acp_session_id, value))
        return [
            option.model_dump(by_alias=True, exclude_none=True, mode="json")
            for option in response.config_options
        ]

    def _live_conn(self) -> tuple[Any, str]:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("ACP session is closed")
            conn, acp_session_id = self._conn, self._acp_session_id
        if conn is None or acp_session_id is None:
            raise RuntimeError("ACP session is not ready")
        return conn, acp_session_id

    def _await_on_loop(self, coro: Any) -> Any:
        """Run a coroutine on the session loop from a foreign thread and wait.

        Unlike cancel() (fire-and-forget, suppressed teardown races), the set
        calls are request/response: a failure must surface to the HTTP caller,
        so nothing is suppressed here."""
        with self._state_lock:
            loop = self._loop
        if loop is None:
            coro.close()
            raise RuntimeError("ACP session loop is not running")
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            # The loop shut down between the state read and the submit.
            coro.close()
            raise
        # No cancel on timeout (PR #393 review): the request already crossed
        # the process boundary — cancelling the local future cannot un-send
        # it, only fake certainty. The caller's 409 says "state uncertain";
        # a late application heals through the agent's own update
        # notification (apply_config_update), and one that never notifies is
        # exactly the drift the notification-path warning logs make visible.
        return future.result(timeout=SET_CONFIG_TIMEOUT_SECONDS)
