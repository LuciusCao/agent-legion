"""Agent-definition payload helpers for the studio-agent tool surface.

Split from ``studio_agent_tools.py`` (#633 file budget, prompted by the
draft-tools router mount): parse the editable payload into the catalog's
``AgentDefinition`` (validation errors map to 422) and shape a version
entity into the API response. Pure functions — no DB access.
"""

from fastapi import HTTPException
from pydantic import ValidationError

from server.app.agent_catalog import AgentDefinition
from server.app.routes.agent_definition_contracts import (
    AgentDefinitionPayload,
    AgentVersionResponse,
)
from server.app.services.versioned_entities import VersionedEntity


def _parse_agent_definition(payload: AgentDefinitionPayload) -> AgentDefinition:
    try:
        return AgentDefinition.model_validate(payload.model_dump())
    except ValidationError as exc:
        # ctx carries the raw exception objects — not JSON serializable.
        detail = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


def _agent_version_response(entity: VersionedEntity) -> AgentVersionResponse:
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
