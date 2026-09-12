"""Contracts for the studio-agent tool surface. Shared shapes are reused from
the sibling contract modules so the tool surface stays shape-compatible with
the Studio UI endpoints it mirrors; tool-only shapes live here. The
agent-definition parse/response helpers (moved from studio_agent_tools.py,
#633, for that composition root's file budget) live here too: they are the
tool surface's adapter between the Studio payload and AgentDefinition.
"""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

import server.app.routes.workflow_contracts as workflow_contracts
from server.app.agent_catalog import AgentDefinition
from server.app.routes.agent_definition_contracts import (
    AgentDefinitionPayload,
    AgentVersionResponse,
)
from server.app.routes.workflow_node_code_contracts import WorkflowNodeCodeDraftRequest
from server.app.routes.workflow_revisions_contracts import WorkflowRevisionSummary
from server.app.services.versioned_entities import VersionedEntity


class StudioAgentActiveWorkflowResponse(BaseModel):
    """``state="empty"`` (not 404) when no default key or no published revision."""

    state: Literal["active", "empty"]
    workflow_key: str | None = None
    revision: WorkflowRevisionSummary | None = None
    workflow: workflow_contracts.WorkflowDefinitionResponse | None = None
    definition_yaml: str | None = None


class StudioAgentNodeCodeDraftRequest(WorkflowNodeCodeDraftRequest):
    """``expected_capability``: validated for existing nodes (mismatch -> 400);
    its presence authorizes a skeleton draft for a not-yet-published node.
    ``min_length=1``: an empty string must not bypass the presence gate."""

    expected_capability: str | None = Field(default=None, min_length=1)


def parse_agent_definition_payload(payload: AgentDefinitionPayload) -> AgentDefinition:
    try:
        return AgentDefinition.model_validate(payload.model_dump())
    except ValidationError as exc:
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


def agent_version_response(entity: VersionedEntity) -> AgentVersionResponse:
    return AgentVersionResponse(
        id=entity.id,
        agent_id=entity.entity_key,
        version=entity.version,
        status=entity.status,
        definition=entity.definition,
        definition_hash=entity.definition_hash,
        created_by=entity.created_by,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )
