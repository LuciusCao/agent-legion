"""Compare-and-set persistence for the Studio workflow YAML draft (#633).

Split from ``workflow_drafts.py`` (budget). #1226 五轮收口（设计 v2）：CAS
基线货币是 workspace 级草稿代次（``workspace_workflow_draft_generation``
的 generation 计数器），不再是 updated_at 时间戳——写入声明它基于的
代次 G，G 落后于当前代次即 ``DraftConflictError``（409），无论草稿行
当前存在与否（行缺失 + G == 当前代次 = 发布后空态上的首存，放行
INSERT；行缺失 + G 落后 = 发布前代次的在途写入，409 不重建）。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.workflow_draft_generation import (
    bump_workflow_draft_generation_if_current,
)
from server.app.jobs.queries.workflow_drafts import DraftConflictError


class WorkflowDraftCasQueriesMixin:
    """CAS upsert for workspace_workflow_drafts (#633; generation-based #1226)."""

    def upsert_workspace_workflow_draft_if_unchanged(
        self,
        workspace_id: str,
        definition_yaml: str,
        base_generation: int,
    ) -> dict[str, Any]:
        """Apply only when the workspace's draft generation still equals the base.

        代次判定在同一事务内三步完成：先对草稿行 FOR UPDATE（等出任何在途
        发布——它的删行 + 代次推进同事务提交），再条件推进代次（无代次行
        时仅 base=0 可建、有行时仅当前代次 == base 可推进，并发交错在
        READ COMMITTED 等待后重评估），推进成功才写草稿行。锁序全程
        「草稿行 → 代次行」，与发布删行同序，无 AB-BA。代次已前进 →
        ``DraftConflictError``（调用方重读新基线重试），不写不推进。
        """
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "select 1 from workspace_workflow_drafts where workspace_id=%s for update",
                (workspace_id,),
            )
            generation = bump_workflow_draft_generation_if_current(
                conn, workspace_id, base_generation
            )
            if generation is not None:
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
        if generation is None:
            current_generation = self.get_workspace_workflow_draft_generation(workspace_id)  # type: ignore[attr-defined]
            raise DraftConflictError(
                "Workflow draft was modified by another session"
                f" (base generation {base_generation},"
                f" current generation {current_generation})"
            )
        return dict(row) | {"generation": generation}
