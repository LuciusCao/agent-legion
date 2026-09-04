"""Agent-publish prune of workspace node overrides (#430).

The workflow-revision publish path prunes workspace overrides against the
new revision's schemas (``node_config_prune``, #428); Agent Definitions
never went through that pipeline, yet they own the config surface of every
``type: agent`` node — the live published ``AgentDefinition.config_schema``,
independent of any revision snapshot. A published Agent that renames/deletes
a property or drops its whole schema left stale override keys behind, and
intake's ``resolve_workflow_node_configs`` re-validates by the LIVE agent
schema, so every new job of that workspace failed at intake (plus the
settings PATCH 400s on the same whitelist) — the same failure shape #428
closed for revision publishes.

Scope of the prune (vs the revision-side one): Agent publish affects the
override sections of every workflow in the published Agent's OWN workspace
whose ACTIVE revision routes an ``agent`` node to the published capability,
and the schema source is the JUST-published definition — never the ~5s
``published_agent_definitions`` process cache, which in this process still
holds the PRE-publish catalog (issue #430's noted multi-process staleness
window is the same tradeoff, only bounded, because this walk re-reads the
section under the write's row lock and re-judges it there).

Transaction stance: the prune runs AFTER the publish transaction commits,
one short transaction per workflow section, and a failure is logged and
swallowed — the Agent definition itself is validly published; failing the
publish over a downstream cleanup would strand a published Agent behind a
500 with the same stale overrides still in place (a strictly worse version
of the 「published but API errored」 window #428 documented). The residual
risk is bounded: a failed prune leaves the workspace exactly where it was
before this fix, and the next Agent publish or workflow revision publish
retries the prune.

Lives beside AgentService (not inside): that service sits exactly at its
budget ceiling, and this orchestration is a publish-side concern with its
own test surface — the same split precedent as ``agent_definition_create``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from server.app.agent_catalog import AgentDefinition
from server.app.db.dialect import ConnectSource
from server.app.services.node_config import workflow_node_config_schemas
from server.app.services.node_config_prune_logic import prune_workflow_override_section
from server.app.services.versioned_entities import VersionedEntityStore
from server.app.services.workflow_definitions import workspace_revision_definition

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


def prune_agent_overrides(
    connect_source: ConnectSource,
    workspace_id: str,
    definition: AgentDefinition,
) -> int:
    """Prune overrides of agent nodes routed to ``definition`` after publish.

    Walks the workspace's override sections (``node_config_json`` is keyed
    by workflow), and for every workflow whose ACTIVE revision still routes
    an ``agent`` node to the published capability, re-prunes that workflow's
    section by the just-published schema through the SAME pure pruner as
    the revision-side publish (#428). Sections of workflows that do not
    route the capability are left untouched — the same node key in an
    unrelated workflow is judged by ITS workflow's schemas, and a workflow
    without an active revision has nothing resolving against the new Agent.
    Returns the number of workflow sections rewritten; failures are logged
    (see module docstring), never raised past the publish.

    ``connect_source`` follows the ConnectSource contract (#187): the
    facade in production wiring; a bare DSN (test/CLI instantiations of
    AgentService) skips the prune — the walk needs the facade's queries
    and BOUNDARY-DATA-001 forbids own connections here. A DSN caller loses
    nothing it had before this mechanism existed.
    """
    if not hasattr(connect_source, "get_workspace"):
        return 0
    job_db = cast(Any, connect_source)
    # The catalog to judge by: the workspace's published Agents with the
    # just-published definition overlaid on its capability — never read
    # through the ~5s published_agent_definitions cache (this process's
    # entry was invalidated, but the walk must not depend on that timing).
    # Sibling agents keep their schemas: an Agent publish must not treat
    # another capability's overrides as schema-less (#428 P1-2 applies only
    # when the node's OWN schema is gone).
    catalog = {
        agent_id: d
        for agent_id, d in _published_catalog(job_db, workspace_id).items()
        if d.capability != definition.capability
    }
    catalog["__publishing__"] = definition
    rewritten = 0
    try:
        for workflow_key, overrides in _workspace_override_sections(job_db, workspace_id):
            if not overrides:
                continue
            active_definition = workspace_revision_definition(
                job_db.get_active_workflow_revision(workspace_id, workflow_key)
            )
            if active_definition is None or not _routes_capability(
                active_definition, definition.capability
            ):
                continue
            rewritten += _prune_one_section(
                job_db, workspace_id, workflow_key, active_definition, catalog, overrides
            )
    except Exception:  # noqa: BLE001 — see module docstring: publish stays green
        # #204 broad-except audit: the prune is a best-effort post-commit
        # cleanup riding a validly published Agent definition; failing the
        # publish over it would strand the publish behind a 500 with the
        # same stale overrides still in place. Failure semantics: the
        # overrides persist exactly as before this fix (that workspace's
        # intake keeps failing until the next Agent/revision publish
        # retries); logged with workspace/capability context for triage.
        logger.exception(
            "agent publish override prune failed (workspace=%s capability=%s)",
            workspace_id,
            definition.capability,
        )
    return rewritten


def _prune_one_section(
    job_db: JobQueries,
    workspace_id: str,
    workflow_key: str,
    active_definition: Any,
    catalog: Mapping[str, AgentDefinition],
    overrides: dict[str, dict[str, Any]],
) -> int:
    """Prune (if needed) one workflow's section; 1 when rewritten, else 0.

    ``overrides`` is only the pre-transaction plan view (a cheap does-
    anything-need-pruning check); the actual write re-reads the section
    under the workspace row lock taken by the section read/write pair, on
    the transaction's own connection, and RE-computes the prune from that
    locked view — the #428 P1-1 rule: a settings PATCH racing between plan
    and lock must have its committed values judged by the new schema (legal
    keys stay, violations go), not be flattened by a pre-lock snapshot
    write-back.

    ``catalog`` is the full published catalog with the just-published
    definition overlaid: nodes routed to OTHER capabilities keep their own
    agents' schemas during this prune — an Agent publish must not treat a
    sibling agent node's override as schema-less (#428 P1-2 clears those
    only when the node's own schema is gone).
    """
    schemas = workflow_node_config_schemas(active_definition, catalog)
    node_keys = frozenset(active_definition.nodes)
    if prune_workflow_override_section(overrides, schemas, node_keys) == overrides:
        return 0
    with job_db.connect() as conn:
        locked = job_db.read_workspace_node_config_section(conn, workspace_id, workflow_key)
        pruned = prune_workflow_override_section(locked, schemas, node_keys)
        if pruned != locked:
            job_db.write_workspace_node_config_section(conn, workspace_id, workflow_key, pruned)
            return 1
    return 0


def _published_catalog(job_db: JobQueries, workspace_id: str) -> dict[str, AgentDefinition]:
    """The workspace's published Agents keyed by agent_id, store-direct.

    Bypasses the ~5s ``published_agent_definitions`` cache on purpose: the
    walk needs the post-publish truth, and the cache may still serve this
    process's pre-publish snapshot within its TTL window.
    """
    return {
        e.entity_key: AgentDefinition.model_validate(e.definition)
        for e in VersionedEntityStore(job_db, "agent").list_published(workspace_id)
    }


def _workspace_override_sections(
    job_db: JobQueries, workspace_id: str
) -> list[tuple[str, dict[str, dict[str, Any]]]]:
    """The workspace's non-empty (workflow_key → node overrides) sections.

    Only workflows with a stored section can carry overrides to prune; a
    workspace without overrides skips the whole walk (the common case —
    most publishes touch nothing).
    """
    workspace = job_db.get_workspace(workspace_id)
    node_config = workspace.get("node_config") if workspace else None
    if not isinstance(node_config, dict):
        return []
    sections = []
    for workflow_key, overrides in node_config.items():
        if not isinstance(overrides, dict) or not overrides:
            continue
        copied = {str(k): dict(v) for k, v in overrides.items() if isinstance(v, dict)}
        if copied:
            sections.append((str(workflow_key), copied))
    return sections


def _routes_capability(active_definition: Any, capability: str) -> bool:
    """Does the active revision route an ``agent`` node to the capability?"""
    return any(
        node.node_type == "agent" and node.capability == capability
        for node in active_definition.nodes.values()
    )
