"""Shared helpers for the studio publish-request handshake (#429 三轮).

Split from services/studio_publish_requests.py (file budget): the wire-
payload shaping, the lazy-expiry timestamp comparison, and the draft-version
token are used by the service layer but carry no state-machine semantics of
their own. The confirm 执行期心跳（#464，续租 claimed_at 防误回收）also
lives here（_confirming_claim_heartbeat）——纯执行期辅助，无自身状态机。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import ConflictError

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

# #429 四轮 P1: how long a ``confirming`` row may sit before readers treat
# it as a dead process's claim (the confirm died between claim and resolve).
# A healthy publish completes in seconds; 5 minutes absorbs slow disk/
# pagination without ever sweeping a live claim.
CONFIRMING_STALE_SECONDS = 300

# #464：claim 存活期内的心跳间隔。300s 过期阈值 ÷ 5 = 每 60s 续租一次，
# 连续漏跳三拍以内都远够不到阈值（与 executor lease 的 interval<TTL/3
# 纪律同源，见 executors/runtime.py 的 10s/90s 配比）。
_CLAIM_HEARTBEAT_INTERVAL_SECONDS = CONFIRMING_STALE_SECONDS / 5


@contextmanager
def _confirming_claim_heartbeat(job_db: JobQueries, request_id: str):
    """#464：confirm 执行期的 claimed_at 续租窗口。

    claim → publish → resolve 之间没有事务包住整段执行（跨进程 claim）：
    合法的慢发布（DB 锁/慢存储/大量校验）超过 CONFIRMING_STALE_SECONDS
    时，轮询侧的过期谓词会把仍在执行的 confirming 行误改成 expired——
    发布可能仍成功创建 revision，但 resolve 已匹配不到该行。本上下文
    管理器在执行期间起一个守护线程周期性 bump claimed_at（executor
    lease 心跳的同语义），谓词因此只对「claim 后无心跳」的死进程生效。

    心跳失败不中断发布（与 lease 心跳的容错纪律一致）：单次续租写失败
    （DB 抖动）只记日志——发布本身仍可能秒级完成并正常 resolve，心跳
    只是延长存活窗口的辅助通道，把它做成失败点反而把罕见的 DB 抖动放大
    成发布失败。行被并发 resolve/过期后心跳自然停跳（谓词在续租语句里
    重查 status='confirming'，rowcount=0 即退出循环）。
    """
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_db, request_id, stop),
        name=f"publish-request-heartbeat-{request_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_CLAIM_HEARTBEAT_INTERVAL_SECONDS + 5)


def _heartbeat_loop(job_db: JobQueries, request_id: str, stop: threading.Event) -> None:
    """续租循环：间隔一拍一跳，谓词失配（行已非 confirming）即退出。"""
    while not stop.wait(_CLAIM_HEARTBEAT_INTERVAL_SECONDS):
        try:
            if not job_db.heartbeat_confirming_publish_request(request_id):
                return
        except Exception:
            # #204 broad-except audit: 守护线程的生命支持（不吞错会死线程，
            # 续租静默停止、慢发布重新暴露给误回收）。DB 层的异常面不可
            # 枚举（连接抖动/锁超时/驱动 quirk），单跳失败只记日志继续。
            logger.exception("publish-request heartbeat failed for %s (continuing)", request_id)


def workspace_draft_yaml(job_db: JobQueries, workspace_id: str) -> str:
    """The workspace's unpublished draft YAML (schema v61 draft store) — the
    same YAML the Studio canvas edits and the review dialog's compare runs
    against; confirm publishes what the human reviewed."""
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    if draft is None:
        raise ConflictError("No unpublished workflow draft to publish")
    return str(draft["definition_yaml"])


def refuse_stale_draft_claim(
    job_db: JobQueries, workspace_id: str, request_id: str, draft_yaml: str
) -> None:
    """Why a confirm claim failed, when it matters: the named row is still
    pending but the server draft is no longer the one the agent requested
    (#429 三轮 P1-3) — the confirm must refuse loudly instead of publishing
    a draft the human never reviewed. No-op for every other failure shape
    (missing/terminal/expired rows stay 404)."""
    pending = job_db.get_pending_publish_request(workspace_id)
    if pending is None or pending["id"] != request_id:
        return
    if pending.get("draft_hash") is not None and pending["draft_hash"] != (
        draft_yaml_hash(draft_yaml)
    ):
        raise ConflictError(
            "Draft changed after the publish request was created;"
            " ask the agent to re-request the publish"
        )


def draft_yaml_hash(draft_yaml: str) -> str:
    """The draft-version token (#429 三轮 P1-3): sha256 of the raw draft
    YAML. Recorded when the agent parks its request and re-checked when the
    human confirms, so the confirmed publish is exactly the draft the agent
    asked about — never a newer save that landed in between. Raw-string
    hashing (not ``definition_hash``'s canonical form): the draft store
    persists the literal YAML, and byte-identity of that literal is the
    binding that matters here."""
    return hashlib.sha256(draft_yaml.encode("utf-8")).hexdigest()


def iso_payload(request: dict[str, Any]) -> dict[str, Any]:
    """The wire payload: timestamps as ISO strings (datetimes otherwise leak
    Postgres-specific formatting into the MCP tool text)."""
    payload = dict(request)
    for field in ("created_at", "expires_at", "resolved_at", "claimed_at"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            payload[field] = value.isoformat()
    return payload


def is_past_expiry(request: dict[str, Any]) -> bool:
    """Whether a pending row is past its ``expires_at``. The driver hands
    timestamptz back as an ISO string in this deployment, so parse (always
    self-produced UTC ISO; ``fromisoformat`` round-trips it)."""
    expires_at = request.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    return expires_at <= datetime.now(UTC)


def is_stale_claim(request: dict[str, Any]) -> bool:
    """Whether a ``confirming`` row's claim is past the stale threshold
    (#429 四轮 P1): the claiming process is presumed dead. String-tolerant
    like ``is_past_expiry``. A row without ``claimed_at`` reads as NOT
    stale — the safe default never risks expiring a live claim."""
    claimed_at = request.get("claimed_at")
    if claimed_at is None:
        return False
    if isinstance(claimed_at, str):
        claimed_at = datetime.fromisoformat(claimed_at)
    return claimed_at <= datetime.now(UTC) - timedelta(seconds=CONFIRMING_STALE_SECONDS)


def may_read_request(job_db: JobQueries, request: dict[str, Any], user: dict[str, Any]) -> bool:
    """Authorization for the agent's status tool, mirroring
    build_session_context: a workspace-bound token reads its own workspace;
    an unbound token needs workspace membership (admin passes)."""
    bound = user.get("scoped_workspace_id")
    if bound is not None:
        return bool(request["workspace_id"] == bound)
    if user.get("role") == "admin":
        return True
    return job_db.get_workspace_role(str(request["workspace_id"]), str(user["id"])) is not None


def active_revision_id(job_db: JobQueries, workspace_id: str) -> str | None:
    """The workspace's active revision id (None when there is none) — the
    before/after probe the confirm uses to attribute result_revision_id."""
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        return None
    workflow_key = str(workspace.get("default_workflow_key") or "")
    revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    return str(revision["id"]) if revision is not None else None
