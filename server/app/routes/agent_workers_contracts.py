"""Pydantic contracts for the Agent Worker control-plane routes."""

from typing import Any

from pydantic import BaseModel, Field

from server.app.agent_workers import MODEL_RUNTIME_PROTOCOL_VERSION


class RegisterAgentWorkerRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    runtimes: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    models: list[dict[str, str]] = Field(default_factory=list)
    max_concurrency: int = Field(gt=0, le=1024)
    # Code-execution capacity pool (batch 2); 0/absent = agent-only Worker.
    max_code_concurrency: int = Field(default=0, ge=0, le=1024)
    labels: dict[str, Any] = Field(default_factory=dict)
    protocol_version: int = Field(default=1, ge=1)
    # Informational only: no agent_workers column stores it yet.
    image_version: str = Field(default="", max_length=128)


class AgentWorkerWorkspace(BaseModel):
    workspace_id: str
    workspace_name: str
    # Ids of the presented register tokens that opened this workspace, so the
    # Worker console can associate each token card with its workspace.
    token_ids: list[str] = Field(default_factory=list)


class RegisterAgentWorkerResponse(BaseModel):
    worker_token: str
    host_protocol_version: int = MODEL_RUNTIME_PROTOCOL_VERSION
    # Server-resolved workspace admission scope; [] means all workspaces.
    allowed_workspaces: list[str]
    # Same scope enriched with workspace names (one row per presented token's
    # workspace, deduplicated) so the Worker console can label each token.
    workspaces: list[AgentWorkerWorkspace] = Field(default_factory=list)


class CreateAgentRegisterTokenRequest(BaseModel):
    # Required: the all-workspaces token variant was retired with the global
    # register token (issue #35).
    workspace_id: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=128)


class AgentRegisterTokenCreatedResponse(BaseModel):
    token_id: str
    # Plaintext, returned exactly once at issuance.
    register_token: str
    workspace_id: str | None
    label: str


class AgentRegisterTokenSummary(BaseModel):
    token_id: str
    workspace_id: str | None
    label: str
    created_at: str
    revoked: bool


class AgentRegisterTokensResponse(BaseModel):
    tokens: list[AgentRegisterTokenSummary]


class AgentRegisterTokenDeleteResponse(BaseModel):
    token_id: str
    deleted: bool
    # Workers whose registration records were cascade-deleted in the same
    # transaction (no live key left); their credentials die immediately.
    cascaded_worker_ids: list[str] = Field(default_factory=list)


class ClaimAgentExecutionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    # Live re-declaration of the worker's machine-wide capacity: the Host
    # records it as the enforced max_concurrency, so dynamic resizes on the
    # worker take effect without re-registration.
    max_concurrency: int | None = Field(default=None, gt=0, le=1024)
    # Live re-declaration of the code-execution pool (batch 2); None leaves
    # the recorded value untouched.
    max_code_concurrency: int | None = Field(default=None, ge=0, le=1024)


class AgentWorkerSummary(BaseModel):
    worker_id: str
    name: str
    runtimes: list[str]
    capabilities: list[str]
    models: list[dict[str, str]]
    max_concurrency: int
    max_code_concurrency: int
    labels: dict[str, str]
    protocol_version: int
    # Server-side workspace admission scope; [] means all workspaces.
    allowed_workspaces: list[str]
    # Ids of the workspace-scoped register tokens that admitted the latest
    # (re)registration — the worker↔key binding shown in the admin UI. [] for
    # workers registered before schema v59.
    register_token_ids: list[str] = Field(default_factory=list)
    registered_at: str
    last_seen_at: str
    # True while the Worker's last authenticated call is within the online
    # threshold; registered-but-silent Workers show as offline.
    online: bool
    revoked: bool


class AgentWorkersResponse(BaseModel):
    workers: list[AgentWorkerSummary]


class AgentWorkerDeleteResponse(BaseModel):
    worker_id: str
    deleted: bool


class AgentClaimResponse(BaseModel):
    execution_id: str
    lease_id: str
    workspace_id: str
    job_id: str
    workflow_key: str
    node_key: str
    agent_id: str
    # 'agent' (default) or 'code' (batch 2): code claims carry a
    # self-contained code payload in the manifest and the Worker executes it
    # through the velites sandbox instead of an Agent runtime.
    kind: str = "agent"
    manifest: dict[str, Any]
    bundle_url: str


class AgentHeartbeatResponse(BaseModel):
    """Protocol v2 heartbeat body: explicit cancellations for this Worker.

    Only kind='code' executions are listed (batch 2 decision 6); v1 Workers
    get the legacy empty 204 instead."""

    cancelled_execution_ids: list[str]
