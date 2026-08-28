"""AcpSessionCallbacks bridge binding a StudioChatService to one session id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService


class ServiceCallbacks:
    """Forward ACP session-thread callbacks into the service with the
    session id bound, so AcpSessionHandle stays service-agnostic."""

    def __init__(self, service: StudioChatService, session_id: str) -> None:
        self._service = service
        self._session_id = session_id
        # Bound by spawn_session_runtime before handle.start(): the death-echo
        # on_exit pins this runtime identity so a stale exit from the OLD
        # thread cannot tear down a newer runtime registered by resume (ABA).
        self.runtime: SessionRuntime | None = None

    def on_ready(self, capabilities: dict[str, Any], acp_session_id: str) -> None:
        self._service._on_ready(self._session_id, capabilities, acp_session_id)

    def on_update(self, update: dict[str, Any]) -> None:
        self._service._on_update(self._session_id, update)

    def on_permission_request(
        self, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._service._on_permission_request(self._session_id, tool_call, options)

    def on_turn_end(self, stop_reason: str) -> None:
        self._service._on_turn_end(self._session_id, stop_reason)

    def on_turn_error(self, detail: str) -> None:
        self._service._on_error(self._session_id, detail, fatal=False)

    def on_error(self, detail: str) -> None:
        self._service._on_error(self._session_id, detail, fatal=True)

    def on_exit(self, *, close_initiated: bool) -> None:
        # 走 _events() 直传 expected_runtime：service._on_exit 保持给 #158 存量
        # 调用/测试的兼容签名（expected=None 即弹注册表当前项的原始语义）。
        self._service._events().on_exit(
            self._session_id, close_initiated=close_initiated, expected=self.runtime
        )
