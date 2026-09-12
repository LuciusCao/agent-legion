"""Mid-turn run-token keepalive + invalidation notice for studio chat (#411/#558).

The agent's MCP headers cannot be re-pointed mid-session, so a token that
dies (mid-turn expiry, idle-expiry, admin revoke) kills the tool channel
while the chat main path stays healthy. This module keeps a live token alive
across long turns (renew on each `tool_call` sessionUpdate; threshold wide
enough that a checked-live token always outlives the turn) and, once dead,
escalates the session to error (resume-reachable — ResumeBar /「继续对话」
rebuilds the channel with a fresh token; #558, semantics in
session_escalation.py) then notices it on the timeline. ACP notification
path, never raises.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from server.app.auth.scoped_tokens import renew_scoped_token
from server.app.auth.sessions import hash_token
from server.app.studio_chat.acp_session import PROMPT_TIMEOUT_SECONDS
from server.app.studio_chat.session_escalation import escalate_dead_token_session

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

TOKEN_INVALIDATED_DETAIL = (
    "工具通道已失效（运行凭证过期或被吊销），agent 暂时无法调用平台工具；"
    "点「继续对话」重建工具通道即可恢复，会话上下文将保留。"
)
# A checked-live token must outlive the current turn: the threshold is the
# turn-duration ceiling plus grace, NOT the turn-start 30min one (#411 review).
_KEEPALIVE_RENEW_THRESHOLD = timedelta(seconds=PROMPT_TIMEOUT_SECONDS + 300)


def _token_alive(backend: ServiceBackend, token: str) -> bool:
    """Dead (revoked / expired / user disabled) tokens no longer resolve to a
    user; a live one is slid forward, never revoked or revived — the same
    leaked-token guarantees as turn-start renewal. The slide's rowcount
    closes the check→update race: dying between the SELECT and the UPDATE
    matches zero rows, and one re-check tells "no slide needed" apart from
    "died under us" (#411 review)."""
    token_hash = hash_token(token)
    if backend.db.get_scoped_token_user(token_hash) is None:
        return False
    return renew_scoped_token(backend.db, token, threshold=_KEEPALIVE_RENEW_THRESHOLD) or (
        backend.db.get_scoped_token_user(token_hash) is not None
    )


def keepalive_run_token(backend: ServiceBackend, session_id: str) -> None:
    """Renew the session's run token on a `tool_call` update; notice once dead.

    Runs on EVERY tool_call — token death is only ever detected after it
    happens. The done-flag deduplicates the DEAD path (a resume mints a fresh
    runtime, token, and flag); every step is guarded so a transient DB
    failure retries on the next tool_call. Callers run this AFTER the
    tool_call row append."""
    runtime: SessionRuntime | None = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        if runtime.token_keepalive_done:
            return
    try:
        alive = _token_alive(backend, runtime.token)
    except Exception:
        # #204 broad-except audit: best-effort keepalive on the notification
        # path. The tool_call message is already persisted by the caller, so
        # a transient DB failure must not propagate into it; the TTL is the
        # backstop and the next tool_call retries (flag stays unset).
        logger.warning("studio chat token keepalive check failed for %s", session_id, exc_info=True)
        return
    if alive:
        return
    # #558：先升级后通知——escalate 抛异常（DB 故障）时 flag 未置、直接
    # 重试且不产生重复通知；escalate 成功后 append 失败时状态已是 error
    # （escalate 的守卫对 error 幂等），重试只补通知。两步都在各自的
    # 吞异常边界内，模块的 never-raises 不变量保持成立。
    try:
        escalate_dead_token_session(backend, session_id)
    except Exception:
        # #204 broad-except audit: best-effort escalation on the notification
        # path — a transient DB failure must not propagate into it; the next
        # tool_call retries (flag stays unset, no notice appended yet).
        logger.warning("studio chat session escalation failed for %s", session_id, exc_info=True)
        return
    try:
        backend.store.append_message(
            session_id,
            "status",
            "system",
            {"event": "run_token_invalidated", "detail": TOKEN_INVALIDATED_DETAIL},
        )
    except Exception:
        # #204 broad-except audit: same swallow semantics as the escalation
        # above — a failed append must retry on the next tool_call (flag set
        # only on success below) rather than be permanently lost.
        logger.warning(
            "studio chat run_token_invalidated notice failed for %s", session_id, exc_info=True
        )
        return
    with runtime.lock:
        runtime.token_keepalive_done = True
    # #558（review P1）：健康的 ACP 进程不能悬挂到 backend 重启——error 行
    # 不占会话 cap、前端又无关闭入口，弃置的升级会话会无界累积子进程。
    # request_stop 在当前 turn 结束后经既有 on_exit 路径自清理（不 join——
    # keepalive 跑在 ACP 线程上，join 即自死锁）。
    runtime.handle.request_stop()
