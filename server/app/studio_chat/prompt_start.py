"""Linearize the tiny coroutine-creation claim with the off-loop watchdog."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

from acp.schema import TextContentBlock


def watchdog_fenced(handle: Any) -> bool:
    return handle._watchdog_candidate is not None


def acp_prompt(conn: Any, acp_session_id: str, text: str) -> Callable[[], Awaitable[Any]]:
    return lambda: conn.prompt(acp_session_id, [TextContentBlock(type="text", text=text)])


def prompt_admission_open(handle: Any) -> bool:
    return not (handle._closed or handle._stop_requested or watchdog_fenced(handle))


async def run_prompt_start(
    start_gate: PromptStartGate | None, prompt: Callable[[], Awaitable[Any]]
) -> Any:
    if start_gate is None:
        return await prompt()
    return await start_gate.run(prompt)


class PromptStartGate:
    def __init__(self, allowed: Callable[[], bool]) -> None:
        self._allowed = allowed
        self.mutex = threading.Lock()

    async def run(self, prompt: Callable[[], Awaitable[Any]]) -> Any:
        # The mutex contains only atomic fence reads and coroutine creation. The
        # second read is the final start/fatal linearization point: a candidate
        # installed after it finds an already-admitted RPC for M19 settlement.
        with self.mutex:
            if not self._allowed() or not self._allowed():
                return None
            request = prompt()
        return await request
