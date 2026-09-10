"""The lazy-expiry and manual-supersede writes of studio_publish_requests
(#429 四轮 split).

Split from studio_publish_requests.py (file budget): the write-side
sweeps that are NOT the create/claim transitions — the pending row's lazy
expiry (the poll's observed-expiry path) and the manual publish path's
supersede. The stale-confirming sweep entry point also lives here (the
create transaction runs its own in-line version under the lock; this one
serves the poll read of a workspace whose agent is not re-requesting),
plus the confirming claim's heartbeat renew (#464) that keeps the sweep
from expiring a publish that is legitimately still executing.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.studio_publish_request_claims import _REQUEST_COLUMNS
from server.app.services.studio_publish_request_support import (
    CONFIRMING_STALE_SECONDS,
)


class StudioPublishRequestWriteQueriesMixin(ConnectionQueriesMixin):
    """Lazy expiry / manual supersede / stale-claim sweep; composed into the
    JobQueries facade next to the create/claims/reads mixins."""

    def expire_pending_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """Write-side lazy expiry for a workspace: if the pending row is past
        its TTL, flip it to ``expired`` and return it; None when there is no
        pending row or it is still within its TTL. The write-side
        counterpart of the pure pending read (#429: the 5s poll must not
        generate write transactions, so the read no longer sweeps)."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'"
                " and expires_at < current_timestamp"
                " returning " + _REQUEST_COLUMNS,
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def expire_stale_confirming_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """#429 四轮 P1: flip a STALE ``confirming`` row to ``expired``.

        A confirming row is the confirm's claim — normally resolved within
        seconds (``confirmed`` / rollback to ``pending``). One whose
        ``claimed_at`` is older than ``CONFIRMING_STALE_SECONDS`` belongs to
        a process that died between claim and resolve (deploy restart, kill):
        it is invisible to the pending read, untouchable by cancel/supersede
        predicates, and without this sweep it would wedge the workspace
        forever. The create transaction sweeps in-line (same statements,
        under the advisory lock); this entry point serves the poll-side
        read. The predicate rides the UPDATE (stale = claimed_at < now -
        threshold), so a claim that is still legitimately mid-publish never
        matches. None when there is no stale confirming row."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='confirming'"
                " and claimed_at < current_timestamp"
                f" - interval '{int(CONFIRMING_STALE_SECONDS)} seconds'"
                " returning " + _REQUEST_COLUMNS,
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def heartbeat_confirming_publish_request(self, request_id: str) -> bool:
        """#464：续租一个执行中的 ``confirming`` claim——把 claimed_at 推到
        当前时刻，谓词（claimed_at < now - 300s）随之重置。

        confirm 是跨进程的 claim（claim → publish → resolve 之间没有事务
        包住整段执行）：慢存储/DB 锁下 publish 可能合法地超过 300 秒，若
        执行期间无任何续租，轮询侧的过期谓词会把仍在执行的 confirming 行
        误改成 expired——发布可能仍成功落 revision，但 resolve 已匹配不到
        该行，响应与 agent 状态双双误报。本方法与 executor lease 的
        heartbeat 同语义（executors/_lease_lifecycle.heartbeat_lease）：谓词
        骑在 UPDATE 上（status='confirming'），行已被并发 resolve/过期时
        rowcount=0，返回 False 而非谎报续租成功——调用方（服务层的心跳
        循环）据此停跳即可，不需要撤销什么。
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "update studio_publish_requests set claimed_at=current_timestamp"
                " where id=%s and status='confirming'",
                (request_id,),
            )
            return cursor.rowcount > 0

    def supersede_pending_publish_requests(self, workspace_id: str) -> int:
        """Move the workspace's pending rows to ``superseded``; returns the
        displaced count. Used by the manual publish path (#429): once a
        human publishes through the toolbar button, any pending agent
        request for the same workspace is moot — its content went live, and
        the agent polling the status tool should see ``superseded`` (a
        publish happened, just not through this request) instead of a
        dead-end pending whose review dialog can never be usefully
        confirmed again. A row in ``confirming`` is deliberately left
        alone: its publish is in flight and will record its own outcome
        (#429 三轮 P1-2 — superseding it would recreate the cancel race in
        supersede form).
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "update studio_publish_requests set status='superseded',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'",
                (workspace_id,),
            )
            return cursor.rowcount
