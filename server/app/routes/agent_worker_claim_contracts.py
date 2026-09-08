"""Pydantic contracts for the Agent Worker claim route (issue #546 split).

Split from ``agent_workers_contracts.py`` for the file budget: the claim
request/response family lives next to its route (``agent_worker_claims.py``)
and its response assembly (``agent_worker_claim_response.py``).
"""

from typing import Any

from pydantic import BaseModel, Field


class ClaimAgentExecutionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    # Live re-declaration of the worker's machine-wide capacity: the Host
    # records it as the enforced max_concurrency, so dynamic resizes on the
    # worker take effect without re-registration.
    max_concurrency: int | None = Field(default=None, gt=0, le=1024)
    # Live re-declaration of the code-execution pool (batch 2); None leaves
    # the recorded value untouched.
    max_code_concurrency: int | None = Field(default=None, ge=0, le=1024)
    # Batch claim (issue #546): 1 (default) = the legacy single-claim path
    # with a byte-identical response; >1 promotes up to `limit` executions in
    # ONE transaction and answers BatchAgentClaimResponse (empty batch = the
    # same 204). Pre-#546 Hosts ignore the field and answer a single claim.
    limit: int = Field(default=1, ge=1, le=1024)
    # Per-pool batch caps (the #546 flood shape: an instantaneous-code storm
    # must fill the code pool without spending agent slots); None = the Host
    # capacity view decides per kind.
    agent_limit: int | None = Field(default=None, ge=0, le=1024)
    code_limit: int | None = Field(default=None, ge=0, le=1024)


class AgentClaimResponse(BaseModel):
    execution_id: str
    lease_id: str
    workspace_id: str
    job_id: str
    # #211 Phase 2: the claim's workflow_key equals workspace_id (schema v62
    # binding); Workers read workspace_id. The field stays in the response
    # until the Phase 3/4 removal window so already-shipped Worker images
    # keep parsing the body.
    workflow_key: str = Field(
        description=(
            "Deprecated: equals workspace_id (schema v62); read workspace_id instead. "
            "Removal is tracked in #211 (deprecated field drops by 2026-10-31)."
        ),
        deprecated=True,
    )
    node_key: str
    agent_id: str
    # 'agent' (default) or 'code' (batch 2): code claims carry a
    # self-contained code payload in the manifest and the Worker executes it
    # through the velites sandbox instead of an Agent runtime.
    kind: str = "agent"
    manifest: dict[str, Any]
    bundle_url: str


class BatchAgentClaimResponse(BaseModel):
    """Batch claim answer (#546): requested via
    ``ClaimAgentExecutionRequest.limit`` > 1; an empty batch stays a 204."""

    claims: list[AgentClaimResponse]


# The claim route answers a single claim on the legacy path and a batch on
# the #546 path; the named union keeps the route's response_model a Name
# (the architecture gate's route-contract rule) while OpenAPI gets anyOf.
ClaimRouteResponse = AgentClaimResponse | BatchAgentClaimResponse
