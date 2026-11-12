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
    # #1228 codex P2: toolCallId 生命周期不跨 turn——新 turn 起点清空
    # coalescing 归属表（对齐 stream slots 的 reset；turn 内取消/超时无
    # 终态帧的条目不泄漏，id 复用不误合并旧行）。
    runtime.tool_call_messages.clear()
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
