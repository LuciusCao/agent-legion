"""Entry-point policy for saving a new Agent definition draft (#407).

The studio form for a new Agent no longer collects an agent_id: the
capability names the Agent (one capability, one main draft per workspace),
so this entry derives the entity key from the capability and rejects a
derived id that collides with any existing entity (draft or archived) —
auto-suffixing ids would confuse more than help, and quietly versioning a
published entity or resurrecting an archived one under a fresh save would
be surprising. An explicit agent_id keeps the legacy
``AgentService.save_draft`` semantics (draft overwrite), so old clients and
the MCP path stay source-compatible.

Lives beside AgentService rather than inside it: that service sits exactly
at its file-budget exemption ceiling, and this policy is a save-entry-only
concern — when the #280 ConnectSource split reshuffles the module both can
be merged or re-split in one move.
"""

from __future__ import annotations

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import ConflictError
from server.app.services.versioned_entities import VersionedEntity


def create_agent_draft(
    service: AgentService,
    agent_id: str | None,
    definition: AgentDefinition,
    created_by: str,
) -> VersionedEntity:
    """Save a new draft, deriving agent_id from capability when omitted."""
    if agent_id is None:
        versions = service.list_versions(definition.capability)
        if versions:
            latest = versions[0]
            raise ConflictError(
                f"该 capability 已有 Agent「{latest.entity_key}」（状态：{latest.status}），"
                "请直接编辑该 Agent；确需另建同 capability 的草稿变体，请显式指定 agent_id"
            )
        agent_id = definition.capability
    return service.save_draft(agent_id, definition, created_by)
