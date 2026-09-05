"""Entry-point policy for saving a new Agent definition draft (#407).

The studio form for a new Agent no longer collects an agent_id: the
capability names the Agent (one capability, one main draft per workspace),
so this entry derives the entity key from the capability and rejects a
derived id that collides with any existing entity (any status: draft,
published, or archived) — auto-suffixing ids would confuse more than help,
and quietly versioning a published entity or resurrecting an archived one
under a fresh save would be surprising. Occupancy spans three faces (#460
release review, P1): the latest row per entity, the still-effective
published rows (a draft that renamed the capability hides the published
version from list_latest — letting that through either overwrote the
entity's newer draft when the legacy id matched, or spawned an entity the
published-capability unique index would never let publish), and an entity
already keyed by the capability itself (its latest row may carry a renamed
capability, and save_draft on that key would silently overwrite the draft).
An explicit agent_id keeps the legacy ``AgentService.save_draft`` semantics
(draft overwrite), so old clients and the MCP path stay
source-compatible. Concurrency note: two simultaneous creations converge
safely through save_draft's unique constraint (last write overwrites or
409s), but first-writer protection is not guaranteed under concurrency — a
partial unique index is an entity mechanism change, tracked in the #407
endgame epic.

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
        # explicit-id draft that got published) or hide behind a newer
        # draft/renamed capability — see _capability_occupant for the faces.
        occupant = _capability_occupant(service, definition.capability)
        if occupant is not None:
            raise ConflictError(
                f"该 capability 已有 Agent「{occupant.entity_key}」（状态：{occupant.status}），请直接编辑该 Agent；如确需另建变体，请通过 API 或 MCP 显式指定 agent_id"
            )
        agent_id = definition.capability
    return service.save_draft(agent_id, definition, created_by)


def _capability_occupant(service: AgentService, capability: str) -> VersionedEntity | None:
    """The entity a derived (default-id) save would collide with.

    Scans, in order: the latest row per entity (a pending draft beats the
    published row; archived entities stay visible), the published rows those
    latest rows can hide (#460 P1: published v1 under a renamed draft v2 —
    the published v1 keeps routing), and an entity already keyed by the
    capability itself (save_draft on that key would overwrite its draft even
    when that draft renamed the capability).
    """
    latest = service.list_latest()
    for entity in latest:
        if str(entity.definition.get("capability") or "") == capability:
            return entity
    for entity in service.list_published():
        if str(entity.definition.get("capability") or "") == capability:
            return entity
    return next((e for e in latest if e.entity_key == capability), None)
