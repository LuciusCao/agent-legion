"""Persistence for the Studio workflow YAML draft (schema v61).

One row per workspace (``workspace_workflow_drafts``): the Studio editor's
single-draft model. The plain write path is a single-statement upsert — no
read-then-write — so concurrent legacy (no-base) autosaves degenerate to
last-write-wins instead of losing an update.

#633 adds the compare-and-set variant (``workflow_draft_cas.py``, split for
the budget). #1226 五轮收口（设计 v2）：CAS 的基线货币从 updated_at 时间戳
扶正为 workspace 级草稿代次（``workspace_workflow_draft_generation``，
见 ``workflow_draft_generation.py``）——任何写入声明它基于的代次 G，G
落后于当前代次即 409；代次单调递增、永不下放。发布 = 同事务删行 +
代次 +1，且条件删除（内容仍与发布 YAML 同语义才删，#1226 R2）。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.workflow_draft_generation import (
    bump_workflow_draft_generation,
    lock_workspace_draft_writes,
)


class DraftConflictError(Exception):
    """The workspace's draft generation moved past the writer's base (#633/#1226).

    Raised only by the CAS upsert — a stale base generation means another
    session (human editor, another agent turn) or a publish advanced the
    draft state; the caller must re-read and rebase, never silently
    overwrite.
    """


class WorkflowDraftQueriesMixin(ConnectionQueriesMixin):
    """CRUD for workspace_workflow_drafts (get / legacy LWW upsert)."""

    _DRAFT_COLUMNS = "workspace_id, definition_yaml, created_at, updated_at"

    def get_workspace_workflow_draft(self, workspace_id: str) -> dict[str, Any] | None:
        """The draft row, carrying the workspace's current draft generation."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {self._DRAFT_COLUMNS}"
                ", coalesce((select generation from workspace_workflow_draft_generation"
                " where workspace_id=%s), 0) as generation"
                " from workspace_workflow_drafts where workspace_id=%s",
                (workspace_id, workspace_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_workspace_workflow_draft(
        self, workspace_id: str, definition_yaml: str
    ) -> dict[str, Any]:
        """Legacy no-base LWW upsert: unconditional write + generation bump.

        残余（设计 v2 记明）：无代次的写入无法在发布代次边界上受保护——现行
         写入者（前端控制器 / agent 工具 / node prompt）都走 CAS 代次判定，
         本路径只服务不带 base_generation 的旧客户端。锁序：与 CAS/发布同
         一协议——先取 workspace 级草稿写锁（#1226 复审 P2），否则空草稿上
         与 CAS 的「先代次行后插草稿」倒置成 AB-BA 死锁。
        """
        with self.connect() as conn:
            lock_workspace_draft_writes(conn, workspace_id)
            row = conn.execute(
                """
                insert into workspace_workflow_drafts(workspace_id, definition_yaml)
                values (%s, %s)
                on conflict(workspace_id) do update set
                  definition_yaml=excluded.definition_yaml,
                  updated_at=current_timestamp
                returning workspace_id, definition_yaml, created_at, updated_at
                """,
                (workspace_id, definition_yaml),
            ).fetchone()
            if row is None:
                raise RuntimeError("workflow draft upsert did not return a row")
            generation = bump_workflow_draft_generation(conn, workspace_id)
        return dict(row) | {"generation": generation}
