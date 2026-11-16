"""Lease-carrying queue envelopes and prompt-start admission helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from server.app.studio_chat.prompt_start import PromptStartGate, prompt_admission_open
from server.app.studio_chat.turn_lease import TurnLease


@dataclass(frozen=True)
class QueuedPrompt:
    text: str
    lease: TurnLease | None
    before_start: Callable[[], Any] | None = None


def send_prompt(
    handle: Any,
    text: str,
    lease: TurnLease | None,
    before_start: Callable[[], Any] | None,
    accept: Callable[[], Any] | None,
) -> bool:
    with handle._state_lock:
        if handle._closed or handle._stop_requested:
            return False
        with handle.control_lock:
            if getattr(handle, "fatal_lease", None) is not None:
                return False
        accepted_lease = accept() if accept is not None else None
        if isinstance(accepted_lease, TurnLease):
            lease = accepted_lease
        with handle.control_lock:
            if getattr(handle, "fatal_lease", None) is not None:
                return False
            if lease is not None and not lease._can_start_locked():
                return False
        item = QueuedPrompt(text, lease, before_start) if lease or before_start else text
        handle._queue.put(item)
        return prompt_admission_open(handle)


async def resolve_before_start(
    before_start: Callable[[], Any] | None, lease: TurnLease | None
) -> tuple[bool, TurnLease | None]:
    if before_start is None:
        return True, lease
    started = await asyncio.to_thread(before_start)
    if started is None or started is False:
        return False, None
    return True, started if isinstance(started, TurnLease) else lease


async def run_prompt_start(
    start_gate: PromptStartGate | None,
    prompt: Callable[[], Awaitable[Any]],
    lease: TurnLease | None,
    before_start: Callable[[], Any] | None = None,
) -> Any:
    accepted, active_lease = await resolve_before_start(before_start, lease)
    if not accepted:
        return None
    if start_gate is None:
        return await prompt()
    return await start_gate.run(active_lease, prompt)
