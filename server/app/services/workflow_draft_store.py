"""Service layer for the Studio workflow YAML draft store (schema v61).

Thin pass-through over JobQueries (BOUNDARY-DATA-001): the only business
rule here is that the workspace must exist (404, mirroring the workflow
revisions routes) — the draft row itself is created by the upsert.

The #633 compare-and-set save lives in ``workflow_draft_cas.py`` (split
for the budget). #1226 五轮收口：CAS 基线是草稿代次（generation）。
六轮 codex P1：内容 + 代次必须原子读——``get_workflow_draft_state``
单查询同一快照（空态也返回一行），不要「读行、行缺再另读取代次」。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.workflow_drafts import workflow_draft_identity_hash

__all__ = [
    "attach_draft_identity_hash",
    "get_workflow_draft_state",
    "save_workflow_draft",
]


def attach_draft_identity_hash(draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """#1143：draft 行补上语义身份 hash（不可解析/无内容 → None），响应契约
    直接 model_validate 拾取；GET/PUT/工具面/409 current_draft 共用同一身份源。"""
    if draft is None:
        return None
    yaml = draft["definition_yaml"]
    identity = workflow_draft_identity_hash(str(yaml)) if yaml is not None else None
    return {**draft, "definition_hash": identity}


def get_workflow_draft_state(job_db: JobQueries, workspace_id: str) -> dict[str, Any]:
    """草稿内容 + 代次的原子读（#1226 六轮 codex P1）：单查询同一快照，
    空态也返回一行（内容字段 None + 当前代次），供 GET 空态、409 载荷与
    save_node_prompt 的 CAS base 使用——杜绝拆分读在两读之间被并发写入
    错配。"""
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    state = attach_draft_identity_hash(job_db.get_workspace_workflow_draft_state(workspace_id))
    assert state is not None  # the state read always returns one row
    return state


def save_workflow_draft(
    job_db: JobQueries, workspace_id: str, definition_yaml: str
) -> dict[str, Any]:
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    draft = attach_draft_identity_hash(
        job_db.upsert_workspace_workflow_draft(workspace_id, definition_yaml)
    )
    assert draft is not None  # the upsert always returns a row
    return draft
