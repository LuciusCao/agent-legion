"""One identity for each accepted turn, shared by human and automatic admission."""

import time

from server.app.studio_chat.runtime import SessionRuntime


def open_turn(
    runtime: SessionRuntime,
    text: str,
    *,
    owner: object | None = None,
    message_id: str | None = None,
    prompt: str | None = None,
) -> None:
    """Caller holds runtime.lock; old queue entries cannot settle this identity.

    ``message_id`` / ``prompt`` identify the human message (and the exact
    prompt text sent for it) so an empty verdict can offer a replay (#882).
    """
    runtime.turn_owner = owner if owner is not None else object()
    runtime.stream.reset()
    runtime.loading = False
    runtime.turn_open = True
    runtime.turn_started_at = time.monotonic()
    runtime.turn_update_count = 0
    # #863: background wakeups open with empty text — no user message to lose.
    runtime.turn_background = not text.strip()
    runtime.turn_skip_empty_check = runtime.turn_background or text.lstrip().startswith("/")
    runtime.turn_may_compact = text.lstrip().startswith("/compact")
    runtime.turn_retry_source = (
        (message_id, text, prompt if prompt is not None else text) if message_id else None
    )
    # #882: any new turn supersedes a pending empty-turn replay.
    runtime.empty_turn_retry = None
