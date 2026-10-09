"""Contracts for the Studio workflow YAML draft store (schema v61).

Empty-state convention: GET always returns 200 for an existing workspace,
with ``definition_yaml: null`` / ``updated_at: null`` when no draft was ever
saved — same structured-empty style as the revisions list (``revisions: []``)
rather than a 404, so the editor's first load needs no error-branch handling.
"""

from pydantic import BaseModel, field_validator

from server.app.services.workflow_draft_cas_token import CAS_TIMESTAMP_HINT, parse_cas_timestamp


class WorkflowDraftStoreRequest(BaseModel):
    definition_yaml: str
    # #633 codex review P1-1: the human PUT carries the CAS base (the updated_at
    # the last GET returned, or "never-saved") so an agent-saved draft lost to a
    # human autosave surfaces as a visible 409, not a silent overwrite. Optional:
    # absent/null keeps the legacy last-write-wins semantics (two-tab autosave).
    expected_updated_at: str | None = None

    @field_validator("definition_yaml")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # A blank draft is never meaningful (the editor's draft always
        # carries at least a workflow key); refuse it instead of storing
        # content that would resurrect as a broken draft on the next load.
        if not value.strip():
            raise ValueError("definition_yaml must not be blank")
        return value

    # #633 codex review P2-2: malformed CAS token → 422 here, never the
    # timestamptz cast's 500. None means "no CAS" (last-write-wins).
    @field_validator("expected_updated_at")
    @classmethod
    def _cas_timestamp_or_never_saved(cls, value: str | None) -> str | None:
        if value is None or parse_cas_timestamp(value):
            return value
        raise ValueError(CAS_TIMESTAMP_HINT)


class WorkflowDraftStoreResponse(BaseModel):
    definition_yaml: str | None = None
    updated_at: str | None = None
    # #1143（方案 B）：草稿的语义身份（解析→归一化→sha256，服务端计算）。
    # 同一语义的两份 YAML（agent 原始串 vs 画布重排）得到相同 hash；前端
    # 用它与草稿卡记录的 hash 核对「是否与编辑器一致」，替代逐字节全等。
    # 不可解析的草稿为 null（前端按「无法核对」降级到字符串比较）。
    definition_hash: str | None = None
