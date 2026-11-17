"""Serialize RPC creation with FATAL finalization and carry its owning lease."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

from acp.schema import TextContentBlock

from server.app.studio_chat.normal_terminal import _can_finish_locked
from server.app.studio_chat.turn_lease import TurnLease


def acp_prompt(conn: Any, acp_session_id: str, text: str) -> Callable[[], Awaitable[Any]]:
    return lambda: conn.prompt(acp_session_id, [TextContentBlock(type="text", text=text)])


def prompt_admission_open_locked(handle: Any) -> bool:
    return not (handle._closed or handle._stop_requested or handle.fatal_lease is not None)


def prompt_admission_open(handle: Any) -> bool:
    with handle.control_lock:
        return prompt_admission_open_locked(handle)


class PromptStartGate:
    def __init__(
        self,
        control_lock: threading.Lock,
        rpc_mutex: threading.Lock,
        allowed: Callable[[], bool],
    ) -> None:
        self.control_lock = control_lock
        self.rpc_mutex = rpc_mutex
        self._allowed = allowed

    async def run(self, lease: TurnLease | None, prompt: Callable[[], Awaitable[Any]]) -> Any:
        with self.rpc_mutex:
            with self.control_lock:
                allowed = (
                    _can_finish_locked(lease.handle, lease, lease.runtime)
                    if lease is not None
                    else self._allowed()
                )
                if not allowed:
                    return None
            # Awaitable creation is the RPC-start linearization point. FATAL can
            # only follow it in this same rpc_mutex; await/schedule is outside both locks.
            request = prompt()
        return await request
