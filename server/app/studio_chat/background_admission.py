"""Background-turn DB claim and open-mirror failure boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from server.app.studio_chat.admission_recovery import session_claim_status
from server.app.studio_chat.turn_watchdog import claim_turn_lease

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease


def claim_background_turn(
    service: StudioChatService, session_id: str, runtime: SessionRuntime
) -> TurnLease | None:
    return claim_turn_lease(
        runtime,
        lambda: service.db.claim_studio_chat_turn(session_id),
        claim_status=lambda: session_claim_status(service, session_id),
    )


def open_background_turn(
    runtime: SessionRuntime, owner: TurnLease, open_mirror: Callable[..., None]
) -> None:
    try:
        open_mirror(runtime, "", owner=owner)
    except Exception as exc:
        # #204 broad-except audit: the platform turn claim is already durable;
        # do not release it into an automatic replay after mirror uncertainty.
        owner.fail_claim_uncertain(f"{type(exc).__name__}: {exc}")
        raise


def background_prompt(task_ids: list[str]) -> str:
    return (
        "[Studio 系统通知] 你先前派发的后台子代理任务已到终态："
        + ", ".join(task_ids)
        + "。请处理原生 completion notification，必要时用 TaskOutput 核对结果，"
        "完成验收并向用户汇报；不要重复派发这些任务。"
    )
