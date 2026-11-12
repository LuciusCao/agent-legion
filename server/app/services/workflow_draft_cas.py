"""Compare-and-set save for the Studio workflow YAML draft (#633).

Split from ``workflow_draft_store.py`` (budget): the CAS save serves the
agent draft-save tool AND (#633 codex review P1-1) the human editor's PUT
when it carries ``base_generation`` — the caller passes the draft
generation its last read returned; a draft state that moved on (another
save, or a publish's removal-and-bump) raises a conflict whose payload
carries the CURRENT draft state (so the losing writer can rebase without a
second round-trip) instead of silently overwriting it. #1226 五轮收口
（设计 v2）：代次取代 updated_at 时间戳成为 CAS 基线货币——单调递增、
永不下放，行缺失也不再豁免（发布后空态上的写入同样按代次判定）。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.workflow_drafts import (
    DraftConflictError as CasDraftConflictError,
)
from server.app.services.job_errors import (
    DraftConflictError,
    NotFoundError,
)
from server.app.services.workflow_draft_store import (
    attach_draft_identity_hash,
    get_workflow_draft_state,
)


def _conflict_payload(
    base_generation: int, current: dict[str, Any] | None, current_generation: int
) -> dict[str, Any]:
    """409 detail 的契约化构造（#1177 codex P1/P2）：字段集与
    ``workflow_draft_store_contracts.WorkflowDraftConflictDetail``（路由侧
    OpenAPI responses= 声明的响应模型的 detail 内层）逐字段一致——路由
    测试以模型 dump 为 oracle 钉死两者同步，service 层不 import routes
    包（分层方向）。
    current_draft.generation 始终是 workspace 当前代次（行缺失时也有值——
    发布删行后的重 base 以它为准）；其余字段行缺失时全 null（前端「仅
    解除警示」路径，#1177 V3）。
    """
    return {
        "message": (
            "Workflow draft conflict: another session saved a newer"
            " draft. Re-read the draft, rebase your changes and retry."
        ),
        "base_generation": base_generation,
        "current_draft": {
            "definition_yaml": current["definition_yaml"] if current else None,
            "updated_at": str(current["updated_at"]) if current else None,
            "definition_hash": current.get("definition_hash") if current else None,
            "generation": current_generation,
        },
    }


def save_workflow_draft_if_unchanged(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    base_generation: int,
) -> dict[str, Any]:
    """CAS save (#633; generation-based since #1226 五轮收口).

    ``base_generation`` is the draft generation a previous read returned
    (0 when the workspace never held a draft). A stale base → 409
    (``DraftConflictError``) whose payload carries the current draft state
    for rebasing; a matching base writes the draft and advances the
    generation, atomically.
    """
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    try:
        draft = attach_draft_identity_hash(
            job_db.upsert_workspace_workflow_draft_if_unchanged(
                workspace_id, definition_yaml, base_generation
            )
        )
    except CasDraftConflictError as exc:
        # #1143: current_draft carries the semantic identity hash too — the
        # adopting side (frontend adopt path) restores savedHash from it.
        # #1177 codex P1: payload is contract-shaped (see _conflict_payload).
        # #1226 六轮 codex P1：内容与代次同一快照（行缺失时代次不得另读——
        # 两读之间落进并发保存会把新代次配给 null 内容）。
        state = get_workflow_draft_state(job_db, workspace_id)
        current = state if state["definition_yaml"] is not None else None
        raise DraftConflictError(
            _conflict_payload(base_generation, current, int(state["generation"]))
        ) from exc
    assert draft is not None  # the CAS upsert path returns a row or raises
    return draft
