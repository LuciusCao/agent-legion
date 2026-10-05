"""Schema v93 data migration: inline Agent definitions into agent nodes (#935).

#440 P3 (§3): every legacy ``type: agent`` node of a workspace's active
revision and Studio draft gets the execution profile of the published Agent
it runs today written onto the node itself (``execution.runtime``,
``requires_labels``, ``tools``, ``config_schema``, ``skill`` — rules in
``agent_profile_backfill_rules``). From then on the node is self-contained
(EXEC-AGENT-PROFILE-001) and the publish gate requires every agent node to be.

- **Backup first**: before a source is rewritten (or when it keeps an
  unresolved node), its original text and a per-node report land in
  ``agent_profile_backfill_backups`` — the operator's undo material and the
  migration report (shared definitions expand 1:N, one row per source).
- **Revisions** are rewritten in place: ``definition_hash`` is recomputed
  over the pure definition (``node_code_pins`` and the new
  ``agent_profile_provenance`` sibling stay out of it, the v66 rule), and
  ``agent_profile_provenance: {node_key: {agent_id, version,
  definition_hash, restore}}`` records where each profile came from —
  the workflow upgrade diff uses it so old jobs whose node ran exactly that
  definition are not re-run just because the node was inlined.
- **Drafts** are rewritten only when a node changes (same as v66).
- **Unresolved** nodes (no / several published Agents, archived, draft-only,
  route drift, unportable skill, invalid definition) stay untouched and are
  reported; the publish gate blocks them until the author inlines a profile.
- **Validation**: the rewritten payload must still load through the current
  workflow loader; a node whose inlining breaks it is reverted and reported
  (``backfill_invalid``).

Idempotent: already self-contained nodes are skipped, so a replay writes
nothing. A fresh database has no rows. In-flight jobs keep their own
intake-frozen snapshots and are untouched. No down migration (same as v66):
old binaries read only the execution keys they know and ignore the new
node fields; Agent definitions are not deleted.

Unlike earlier data migrations this one imports two app models lazily:
``AgentDefinition`` (the provenance hash must equal the identity dispatch
records, which only the model defines) and the workflow loader (the
rewritten payload is validated by the very code that will read it).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from functools import partial
from typing import Any

import yaml

from server.app.db.migrations.agent_profile_backfill_rules import (
    LegacyCatalog,
    PublishedAgent,
    Resolution,
    backfill_blocker,
    backfill_draft_node,
    backfill_revision_node,
    effective_runtime,
    provenance_entry,
    resolve_by_capability,
    resolve_routed,
)

logger = logging.getLogger(__name__)

PROVENANCE_KEY = "agent_profile_provenance"
_HASH_EXCLUDED_KEYS = ("node_code_pins", PROVENANCE_KEY)

_BACKUP_DDL = """
create table if not exists agent_profile_backfill_backups (
  id bigserial primary key,
  workspace_id text not null,
  source text not null check(source in ('active_revision', 'draft')),
  source_id text not null,
  original_text text not null,
  report_json text not null,
  created_at timestamptz not null default current_timestamp
)
"""
_ACTIVE_REVISIONS = (
    "select id, workspace_id, definition_json from workflow_revisions"
    " where status='active' order by workspace_id"
)
_DRAFTS = (
    "select workspace_id, definition_yaml from workspace_workflow_drafts order by workspace_id"
)
_AGENT_ROWS = (
    "select entity_key, version, status, definition_json from versioned_entities"
    " where entity_type='agent' and workspace_id=%s order by entity_key, version desc"
)
_AGENT_ROUTES = (
    "select node_key, target_id from workspace_node_routes"
    " where workspace_id=%s and target_kind='agent'"
)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pure_hash(payload: dict[str, Any]) -> str:
    pure = {key: value for key, value in payload.items() if key not in _HASH_EXCLUDED_KEYS}
    return hashlib.sha256(_canonical_json(pure).encode("utf-8")).hexdigest()


def _load_catalog(conn: Any, workspace_id: str) -> LegacyCatalog:
    from server.app.agent_catalog import AgentDefinition

    catalog = LegacyCatalog()
    for row in conn.execute(_AGENT_ROWS, (workspace_id,)).fetchall():
        agent_id = str(row["entity_key"])
        try:
            document = json.loads(str(row["definition_json"]))
        except json.JSONDecodeError:
            document = {}
        capability = str(document.get("capability") or "") if isinstance(document, dict) else ""
        catalog.latest.setdefault(agent_id, (capability, str(row["status"])))
        if row["status"] != "published":
            continue
        try:
            definition = AgentDefinition.model_validate(document)
        except ValueError:
            catalog.invalid.add(agent_id)
            continue
        catalog.published[agent_id] = PublishedAgent(
            agent_id=agent_id,
            version=int(row["version"]),
            capability=definition.capability,
            runtime=definition.runtime,
            tools=tuple(definition.tools),
            requires_labels=dict(definition.requires_labels),
            config_schema=dict(definition.config_schema),
            skill=definition.skill,
            definition_hash=definition.definition_hash(),
        )
    return catalog


def _loads(kind: str, payload: dict[str, Any]) -> bool:
    """True when *payload* loads through the current workflow loader."""
    from server.app.workflows.loader import (
        workflow_definition_from_dict,
        workflow_definition_from_mapping,
    )
    from server.app.workflows.schema import WorkflowDefinitionError

    try:
        if kind == "active_revision":
            workflow_definition_from_dict(copy.deepcopy(payload))
        else:
            workflow_definition_from_mapping(copy.deepcopy(payload))
    except (WorkflowDefinitionError, ValueError):
        return False
    return True


def _node_type(node: dict[str, Any], kind: str) -> str:
    return str(node.get("node_type" if kind == "active_revision" else "type") or "")


def _backfill_payload(
    kind: str,
    payload: dict[str, Any],
    resolve: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Inline every resolvable legacy agent node of *payload* in place.

    Returns (per-node report, provenance entries). Each node is applied to a
    scratch copy first and kept only if the whole payload still loads.
    """
    report: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    nodes = payload["nodes"]
    for node_key in sorted(nodes):
        node = nodes[node_key]
        if not isinstance(node, dict) or _node_type(node, kind) != "agent":
            continue
        if effective_runtime(node, payload.get("execution")):
            continue  # already self-contained (#933)
        entry: dict[str, Any] = {"node_key": node_key, "capability": node.get("capability")}
        resolution: Resolution = resolve(node_key, str(node.get("capability") or ""))
        agent = resolution.agent
        blocker = backfill_blocker(node, agent) if agent is not None else ""
        if agent is None or blocker:
            ids = list(resolution.agent_ids) or ([agent.agent_id] if agent else [])
            report.append(
                {
                    **entry,
                    "status": "unresolved",
                    "reason": resolution.reason or blocker,
                    "agent_ids": ids,
                }
            )
            continue
        candidate = copy.deepcopy(payload)
        declared_schema = dict(node.get("config_schema") or {})
        if kind == "active_revision":
            restore = backfill_revision_node(candidate["nodes"][node_key], agent)
        else:
            backfill_draft_node(candidate["nodes"][node_key], agent)
            restore = {}
        if not _loads(kind, candidate):
            report.append(
                {
                    **entry,
                    "status": "unresolved",
                    "reason": "backfill_invalid",
                    "agent_ids": [agent.agent_id],
                }
            )
            continue
        nodes[node_key] = candidate["nodes"][node_key]
        if kind == "active_revision":
            provenance[node_key] = provenance_entry(agent, restore)
        report.append(
            {
                **entry,
                "status": "backfilled",
                "agent_id": agent.agent_id,
                "agent_version": agent.version,
                "config_schema_overridden": bool(declared_schema)
                and declared_schema != dict(agent.config_schema),
            }
        )
    return report, provenance


def _backup(
    conn: Any, workspace_id: str, kind: str, source_id: str, original: str, report: list
) -> None:
    """Back up a source before it is rewritten, or report its unresolved nodes.

    Idempotent: a replay only ever meets still-unresolved nodes (backfilled
    ones are self-contained now) — when nothing is backfilled and the source
    already has a row, nothing is added.
    """
    if not any(node["status"] == "backfilled" for node in report):
        exists = conn.execute(
            "select 1 from agent_profile_backfill_backups where source=%s and source_id=%s",
            (kind, source_id),
        ).fetchone()
        if exists is not None:
            return
    conn.execute(
        "insert into agent_profile_backfill_backups"
        "(workspace_id, source, source_id, original_text, report_json)"
        " values (%s, %s, %s, %s, %s)",
        (workspace_id, kind, source_id, original, _canonical_json({"nodes": report})),
    )


def _resolve_routed_node(
    routes: dict[str, str], catalog: LegacyCatalog, node_key: str, capability: str
) -> Resolution:
    return resolve_routed(capability, routes.get(node_key), catalog)


def _resolve_draft_node(catalog: LegacyCatalog, _node_key: str, capability: str) -> Resolution:
    return resolve_by_capability(capability, catalog)


def _catalog(conn: Any, catalogs: dict[str, LegacyCatalog], workspace_id: str) -> LegacyCatalog:
    if workspace_id not in catalogs:
        catalogs[workspace_id] = _load_catalog(conn, workspace_id)
    return catalogs[workspace_id]


def _migrate_revisions(conn: Any, catalogs: dict[str, LegacyCatalog]) -> None:
    for revision in conn.execute(_ACTIVE_REVISIONS).fetchall():
        workspace_id = str(revision["workspace_id"])
        original = str(revision["definition_json"])
        try:
            payload = json.loads(original)
        except json.JSONDecodeError:
            logger.warning("v93: active revision %s has corrupt JSON; skipped", revision["id"])
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), dict):
            continue
        if not _loads("active_revision", payload):
            logger.warning("v93: active revision %s does not load; skipped", revision["id"])
            continue
        catalog = _catalog(conn, catalogs, workspace_id)
        routes = {
            str(row["node_key"]): str(row["target_id"])
            for row in conn.execute(_AGENT_ROUTES, (workspace_id,)).fetchall()
        }
        report, provenance = _backfill_payload(
            "active_revision",
            payload,
            partial(_resolve_routed_node, routes, catalog),
        )
        if not report:
            continue
        _backup(conn, workspace_id, "active_revision", str(revision["id"]), original, report)
        if not provenance:
            continue
        payload[PROVENANCE_KEY] = {**(payload.get(PROVENANCE_KEY) or {}), **provenance}
        conn.execute(
            "update workflow_revisions set definition_json=%s, definition_hash=%s where id=%s",
            (_canonical_json(payload), _pure_hash(payload), revision["id"]),
        )


def _migrate_drafts(conn: Any, catalogs: dict[str, LegacyCatalog]) -> None:
    if (
        conn.execute("select to_regclass('workspace_workflow_drafts') as oid").fetchone()["oid"]
        is None
    ):
        return
    for draft in conn.execute(_DRAFTS).fetchall():
        workspace_id = str(draft["workspace_id"])
        original = str(draft["definition_yaml"])
        try:
            payload = yaml.safe_load(original)
        except yaml.YAMLError:
            logger.warning("v93: workspace %s draft is not valid YAML; skipped", workspace_id)
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), dict):
            continue
        if not _loads("draft", payload):
            logger.warning("v93: workspace %s draft does not load; skipped", workspace_id)
            continue
        catalog = _catalog(conn, catalogs, workspace_id)
        report, _ = _backfill_payload("draft", payload, partial(_resolve_draft_node, catalog))
        if not report:
            continue
        _backup(conn, workspace_id, "draft", workspace_id, original, report)
        if not any(node["status"] == "backfilled" for node in report):
            continue
        conn.execute(
            "update workspace_workflow_drafts set definition_yaml=%s, updated_at=current_timestamp"
            " where workspace_id=%s",
            (yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), workspace_id),
        )


def migrate_agent_profile_backfill(conn: Any) -> None:
    """Back up, then inline Agent profiles into active revisions and drafts (v93)."""
    conn.execute(_BACKUP_DDL)
    catalogs: dict[str, LegacyCatalog] = {}
    _migrate_revisions(conn, catalogs)
    _migrate_drafts(conn, catalogs)
    unresolved = conn.execute(
        "select count(*) as n from agent_profile_backfill_backups"
        ' where report_json like \'%"status":"unresolved"%\''
    ).fetchone()["n"]
    if unresolved:
        logger.warning(
            "v93: %s workflow source(s) keep unresolved agent nodes; see"
            " agent_profile_backfill_backups.report_json (publish gate blocks them)",
            unresolved,
        )
