"""Contracts for the Studio workflow YAML draft store (schema v61).

Empty-state convention: GET always returns 200 for an existing workspace,
with ``definition_yaml: null`` / ``updated_at: null`` when no draft was ever
saved — same structured-empty style as the revisions list (``revisions: []``)
rather than a 404, so the editor's first load needs no error-branch handling.
``generation`` is always the workspace's current draft generation (#1226
五轮收口, 设计 v2) — including the post-publish empty state, so a first save
on it can declare its base.
"""

from pydantic import BaseModel, Field, field_validator


class WorkflowDraftStoreRequest(BaseModel):
    definition_yaml: str
    # #633 codex review P1-1 + #1226 五轮收口：PUT 携带 CAS 基线代次（最后
    # 一次读到的 generation），落后即 409（detail 携带当前草稿状态）；
    # 缺省/null = legacy 无代次写入，保留 last-write-wins 放行（设计 v2
    # 记明的兼容残余，现行客户端都携带代次）。
    base_generation: int | None = Field(default=None, ge=0)

    @field_validator("definition_yaml")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # A blank draft is never meaningful (the editor's draft always
        # carries at least a workflow key); refuse it instead of storing
        # content that would resurrect as a broken draft on the next load.
        # #1196：空白判据 = Python str.strip() 全集（含 \x85/\x1c-\x1f，
        # JS trim() 不剥）；前端 isBlankDraftYaml（draftSaveConflict.ts）
        # 按本口径 skip，保证编辑器永不发出此处必拒的内容。
        if not value.strip():
            raise ValueError("definition_yaml must not be blank")
        return value


class WorkflowDraftStoreResponse(BaseModel):
    definition_yaml: str | None = None
    updated_at: str | None = None
    # #1143（方案 B）：草稿的语义身份（解析→归一化→sha256，服务端计算）。
    # 同一语义的两份 YAML（agent 原始串 vs 画布重排）得到相同 hash；前端
    # 用它与草稿卡记录的 hash 核对「是否与编辑器一致」，替代逐字节全等。
    # 不可解析的草稿为 null（前端按「无法核对」降级到字符串比较）。
    definition_hash: str | None = None
    # #1226 五轮收口：workspace 当前草稿代次（CAS 基线货币）。
    generation: int = 0


class WorkflowDraftConflictCurrentDraft(BaseModel):
    """409 conflict payload 的 ``detail.current_draft``（#1177 codex P1：
    契约模型化——此前是裸 dict，前端只能手写 transport type）。

    与 ``WorkflowDraftStoreResponse`` 同一字段集（服务端 current draft 的
    单一形状）：草稿行不存在（发布删除后的代次冲突）时 yaml/时间戳/hash
    全 null，但 ``generation`` 始终是 workspace 当前代次（重 base 以它为
    准）。
    """

    definition_yaml: str | None = None
    updated_at: str | None = None
    definition_hash: str | None = None
    generation: int = 0


class WorkflowDraftConflictDetail(BaseModel):
    """409 detail 顶层（服务层 DraftConflictError.payload 的契约形态）：
    message（人读指引）+ base_generation（写入方声明的落后基线）+
    current_draft（含当前代次）。"""

    message: str
    base_generation: int
    current_draft: WorkflowDraftConflictCurrentDraft


class WorkflowDraftConflictResponse(BaseModel):
    """409 响应体（#1177 codex P2）：app 级异常处理器把 payload 交给
    ``HTTPException(detail=...)``，FastAPI 原生 handler 固定渲染为
    ``{"detail": ...}``——OpenAPI 声明必须带这层封套，否则生成的
    客户端（前端 transport type 由其派生）按错误的形状读响应。"""

    detail: WorkflowDraftConflictDetail
