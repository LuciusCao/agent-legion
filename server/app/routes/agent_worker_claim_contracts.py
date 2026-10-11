"""Pydantic contracts for the Agent Worker claim route (issue #546 split).

Split from ``agent_workers_contracts.py`` for the file budget: the claim
request/response family lives next to its route (``agent_worker_claims.py``)
and its response assembly (``agent_worker_claim_response.py``).
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

from shared.concurrency_limits import MAX_DYNAMIC_CONCURRENCY

# #1158 Worker 节点级并发上限的声明通道类型：key = 裸 node_key（不带
# workspace，机器保护语义），value = 正整数上限。
NodeConcurrencyLimits = dict[
    Annotated[str, Field(min_length=1, max_length=128)],
    Annotated[int, Field(gt=0, le=MAX_DYNAMIC_CONCURRENCY)],
]


class ClaimAgentExecutionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    # Live re-declaration of the worker's machine-wide capacity: the Host
    # records it as the enforced max_concurrency, so dynamic resizes on the
    # worker take effect without re-registration.
    max_concurrency: int | None = Field(default=None, gt=0, le=MAX_DYNAMIC_CONCURRENCY)
    # Live re-declaration of the code-execution pool (batch 2); None leaves
    # the recorded value untouched.
    max_code_concurrency: int | None = Field(default=None, ge=0, le=MAX_DYNAMIC_CONCURRENCY)
    # #1158 Worker 节点级并发上限（机器资源保护层）：每次 claim 重声明，
    # Host 热同步进 agent_workers 并在 claim 判定按 (worker_id, node_key)
    # 计数强制。None（旧 Worker 不声明）= 保留库存值；{} = 显式清空（无限制）。
    node_concurrency_limits: NodeConcurrencyLimits | None = Field(default=None, max_length=256)
    # Batch claim (issue #546; single path retired by #547): promotes up to
    # `limit` executions in ONE transaction and answers
    # BatchAgentClaimResponse (empty batch = the same 204). The default 1
    # answers a one-element claims list.
    limit: int = Field(default=1, ge=1, le=MAX_DYNAMIC_CONCURRENCY)
    # Per-pool batch caps (the #546 flood shape: an instantaneous-code storm
    # must fill the code pool without spending agent slots); None = the Host
    # capacity view decides per kind.
    agent_limit: int | None = Field(default=None, ge=0, le=MAX_DYNAMIC_CONCURRENCY)
    code_limit: int | None = Field(default=None, ge=0, le=MAX_DYNAMIC_CONCURRENCY)


class AgentClaimResponse(BaseModel):
    execution_id: str
    lease_id: str
    workspace_id: str
    job_id: str
    node_key: str
    agent_id: str
    # 'agent' (default) or 'code' (batch 2): code claims carry a
    # self-contained code payload in the manifest and the Worker executes it
    # through the velites sandbox instead of an Agent runtime.
    kind: str = "agent"
    # EXEC-GENERATION-001 (observational): the request row's execution epoch,
    # CAS-verified against jobs.execution_generation at claim time. Workers
    # may log it; the Host remains the only enforcer.
    execution_generation: int = 0
    manifest: dict[str, Any]
    bundle_url: str


class BatchAgentClaimResponse(BaseModel):
    """Batch claim answer (#546; the route's only shape since #547 retired
    the single-object path): ``claims`` holds 0..limit items, an empty batch
    stays a 204. The default ``limit=1`` answers a one-element list."""

    claims: list[AgentClaimResponse]
