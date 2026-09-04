"""Entry-point policy for saving a new Agent definition draft (#407).

The studio form for a new Agent no longer collects an agent_id: the
capability names the Agent (one capability, one main draft per workspace),
so this entry derives the entity key from the capability and rejects a
derived id that collides with any existing entity (any status: draft,
published, or archived) — auto-suffixing ids would confuse more than help,
and quietly versioning a published entity or resurrecting an archived one
under a fresh save would be surprising. An explicit agent_id keeps the
legacy ``AgentService.save_draft`` semantics (draft overwrite), so old
clients and the MCP path stay source-compatible. Concurrency note: two
simultaneous creations converge safely through save_draft's unique
constraint (last write overwrites or 409s), but first-writer protection is
not guaranteed under concurrency — a partial unique index is an entity
mechanism change, tracked in the #407 endgame epic.

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
        # An occupant may be keyed differently from the capability (an
        # explicit-id draft that got published): scan latest rows per entity.
        occupant = next(
            (
                e
                for e in service.list_latest()
                if str(e.definition.get("capability") or "") == definition.capability
            ),
            None,
        )
        if occupant is not None:
            raise ConflictError(
                f"该 capability 已有 Agent「{occupant.entity_key}」（状态：{occupant.status}），请直接编辑该 Agent；如确需另建变体，请通过 API 或 MCP 显式指定 agent_id"
            )
        agent_id = definition.capability
    return service.save_draft(agent_id, definition, created_by)
