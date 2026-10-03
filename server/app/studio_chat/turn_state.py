"""One identity for each accepted turn, shared by human and automatic admission."""

import time

from server.app.studio_chat.runtime import SessionRuntime


def open_turn(runtime: SessionRuntime, text: str, *, owner: object | None = None) -> None:
    """Caller holds runtime.lock; old queue entries cannot settle this identity."""
    runtime.turn_owner = owner if owner is not None else object()
    runtime.stream.reset()
    runtime.loading = False
    runtime.turn_open = True
    runtime.turn_started_at = time.monotonic()
    runtime.turn_update_count = 0
    # #863: background wakeups open with empty text — no user message to lose.
    runtime.turn_skip_empty_check = not text.strip() or text.lstrip().startswith("/")
    runtime.turn_may_compact = text.lstrip().startswith("/compact")
