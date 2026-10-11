"""Draft-generation state machine for the Studio workflow draft (#1226 五轮收口).

设计 v2（issue #1221, 2026-10-11）：每 workspace 一格草稿状态机
``∅ 无草稿`` / ``D(content, gen)``；发布 = 代次递增 + 行删除。唯一不变量：
**任何写入都必须声明它基于的代次 G；G 落后于当前代次即 409。** 代次单调
递增、永不下放。``workspace_workflow_draft_generation`` 是代次计数器
（无行 = 代次 0），每次草稿写/发布删行 +1。

取代五轮的 tombstone（记被删行 updated_at 的近似）：代次同时覆盖
「行存在被并发推进」与「行被发布删除」两格，且 never-saved 不再豁免
（R6：读到空草稿的代次也是代次，落后即 409）。legacy 无代次写入
（不带 base_generation 的旧客户端）保持 LWW 放行——已知残余：它无法在
发布代次边界上受保护，所有现行写入者都携带代次。

锁序：草稿行（FOR UPDATE / upsert）→ 代次行（bump），写路径与发布删行
同序，无 AB-BA。条件 bump 的 UPDATE 分支在 READ COMMITTED 下等待后重评估
WHERE，并发写/发布交错两个方向都安全。

Mixin 持有池化读；conn-first 帮手（bump / 条件 bump / 发布生命周期的
锁读与删除）由同模块与兄弟模块在调用方事务上执行——同
``workflow_revision_projection`` 惯例。
"""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseConnection

_DRAFT_COLUMNS = "workspace_id, definition_yaml, created_at, updated_at"


def bump_workflow_draft_generation(conn: DatabaseConnection, workspace_id: str) -> int:
    """Unconditional +1 (publish's row delete; legacy no-base LWW saves)."""
    row = conn.execute(
        """
        insert into workspace_workflow_draft_generation(workspace_id, generation)
        values (%s, 1)
        on conflict(workspace_id) do update set
          generation = workspace_workflow_draft_generation.generation + 1
        returning generation
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:  # insert...on conflict always returns a row
        raise RuntimeError("draft generation bump did not return a row")
    return int(row["generation"])


def bump_workflow_draft_generation_if_current(
    conn: DatabaseConnection, workspace_id: str, base_generation: int
) -> int | None:
    """+1 only when the current generation equals ``base_generation``.

    两步：base 为 0 时先确保代次行存在（无行 = 代次 0，首个写入者的并发
    首存需要一个可锁的目标行），再按「当前代次 == base」条件推进——
    UPDATE 的 WHERE 在 READ COMMITTED 下等待行锁后重评估，并发写/发布
    交错两个方向都只有一方推进。base 落后（或无行而 base>0）→ 无返回行
    → 调用方判 409；base>0 而无行本就是必败格，不兜底插行——被拒的写入
    不再留下 generation=0 的残余行（#1226 对抗复验 P3）。
    """
    if base_generation == 0:
        conn.execute(
            "insert into workspace_workflow_draft_generation(workspace_id, generation)"
            " values (%s, 0) on conflict(workspace_id) do nothing",
            (workspace_id,),
        )
    row = conn.execute(
        "update workspace_workflow_draft_generation set generation = generation + 1"
        " where workspace_id=%s and generation=%s returning generation",
        (workspace_id, base_generation),
    ).fetchone()
    return int(row["generation"]) if row is not None else None


class WorkflowDraftGenerationQueriesMixin:
    """Pooled draft-generation read + the publish lifecycle's conn-first I/O."""

    def get_workspace_workflow_draft_generation(self, workspace_id: str) -> int:
        """Current draft generation (0 = never written)."""
        with self._connect_read() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "select generation from workspace_workflow_draft_generation where workspace_id=%s",
                (workspace_id,),
            ).fetchone()
        return int(row["generation"]) if row is not None else 0

    def get_workspace_workflow_draft_state(self, workspace_id: str) -> dict[str, Any]:
        """草稿内容 + 代次的原子读取：单查询同一快照（#1226 六轮 codex P1）。

        「读草稿行、行缺失再另读取代次」的拆分读会让并发保存落进两次读
        之间，产出「内容 null + 并发后的新代次」的错配——客户端拿错配
        代次保存会 CAS 成功、静默覆盖它从未看到的草稿。本读法 LEFT JOIN
        两侧都缺席时仍返回一行（内容字段全 null、代次 0），行在时与
        ``get_workspace_workflow_draft`` 同形同源。
        """
        with self._connect_read() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "select d.workspace_id, d.definition_yaml, d.created_at, d.updated_at,"
                " coalesce(g.generation, 0) as generation"
                " from (values (%s)) as w(workspace_id)"
                " left join workspace_workflow_drafts d on d.workspace_id = w.workspace_id"
                " left join workspace_workflow_draft_generation g"
                " on g.workspace_id = w.workspace_id",
                (workspace_id,),
            ).fetchone()
        assert row is not None  # the values row guarantees exactly one result row
        return dict(row)

    def read_workspace_workflow_draft_for_update(
        self, conn: DatabaseConnection, workspace_id: str
    ) -> dict[str, Any] | None:
        """The draft row under a row lock, inside the caller's transaction.

        The publish delete hook reads here before deciding: ``for update``
        serializes against a concurrent draft upsert (READ COMMITTED
        re-evaluates after the lock wait), so the comparison the caller makes
        on the returned content is race-safe. Never commits on its own.
        """
        row = conn.execute(
            f"select {_DRAFT_COLUMNS}"
            " from workspace_workflow_drafts where workspace_id=%s for update",
            (workspace_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def delete_workspace_workflow_draft(
        self, conn: DatabaseConnection, workspace_id: str
    ) -> int | None:
        """Delete the draft row and bump the generation, caller's transaction.

        Publish rides this on the revision write's commit hook: the delete is
        conditional on the locked row still matching the published content
        (decided by the caller, #1226 R2); when it fires, the generation
        advances in the same transaction (#1226 五轮收口) so a pre-publish
        in-flight save conflicts instead of resurrecting the deleted content.
        Returns the new generation; an absent row deletes nothing and bumps
        nothing (no generation boundary without a delete) → None.
        """
        row = conn.execute(
            "delete from workspace_workflow_drafts where workspace_id=%s returning workspace_id",
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return bump_workflow_draft_generation(conn, workspace_id)
