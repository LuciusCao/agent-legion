"""ACP client callbacks, separate from subprocess and queue lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from server.app.studio_chat.terminals import TerminalClientMixin


class AcpClient(TerminalClientMixin):
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
                outcome=AllowedOutcome(outcome="selected", option_id=option_id)
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
