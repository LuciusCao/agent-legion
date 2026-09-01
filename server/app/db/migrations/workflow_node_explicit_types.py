"""Schema v66 data migration: explicit workflow node types (issue #284).

Workflow nodes now declare their execution kind explicitly:
``type: code`` (implicit code pool) or ``type: agent`` (Agent-routed);
the legacy ``node`` spelling and an omitted ``type`` both normalize to
``code`` in the loader. This migration backfills stored payloads so they
match what the loader would produce — otherwise the next save would see a
structural diff and spawn a ghost revision:

- ``workflow_revisions.definition_json`` (active rows): every non-start
  node gets ``node_type: "agent"`` when the materialized
  ``workspace_node_routes`` projection has an agent route row for the same
  (workspace, workflow, node), ``"code"`` otherwise. ``definition_hash``
  is recomputed over the pure definition (the publish rule: the
  ``node_code_pins`` sibling key stays out of the hash but is preserved in
  the stored payload).
- ``workspace_workflow_drafts.definition_yaml`` (one row per workspace):
  non-start nodes missing an explicit ``type`` get ``type: agent|code``
  from the same projection. Drafts whose nodes all carry an explicit type
  keep their YAML text byte-identical (comments and formatting survive).

``start`` and ``approval`` nodes already carry explicit types and are
skipped. Idempotent: nodes already carrying ``code``/``agent`` are
skipped, so a replay is a no-op; a fresh database has no rows and is a
natural no-op. In-flight jobs keep their own intake-frozen snapshots and
are untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import yaml

from server.app.db.migrations.retire_workflow_key_columns import has_column

logger = logging.getLogger(__name__)

_ACTIVE_REVISIONS = (
    "select id, workspace_id, workflow_key, definition_json from workflow_revisions"
    " where status='active' order by workspace_id, workflow_key"
)
_AGENT_ROUTE_NODES = (
    "select node_key from workspace_node_routes"
    " where workspace_id=%s and workflow_key=%s and target_kind='agent'"
)
_WORKSPACE_AGENT_ROUTE_NODES = (
    "select node_key from workspace_node_routes where workspace_id=%s and target_kind='agent'"
)
_DRAFTS_TABLE_PRESENT = "select to_regclass('workspace_workflow_drafts') as oid"
_DRAFTS = "select workspace_id, definition_yaml from workspace_workflow_drafts"

# start/approval already carry an explicit type (EXEC-APPROVAL-001); only
# legacy ``node``/missing values are backfilled.
_TYPED_VALUES = ("start", "approval", "code", "agent")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _agent_route_nodes(conn: Any, workspace_id: str, workflow_key: str) -> set[str]:
    rows = conn.execute(_AGENT_ROUTE_NODES, (workspace_id, workflow_key)).fetchall()
    return {str(row["node_key"]) for row in rows}


def _backfill_revision_payload(payload: dict[str, Any], agent_nodes: set[str]) -> bool:
    """Backfill ``node_type`` in one revision payload; True when anything changed."""
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        return False
    changed = False
    for node_key, node in nodes.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("node_type")
        if node_type in _TYPED_VALUES:
            continue
        node["node_type"] = "agent" if str(node_key) in agent_nodes else "code"
        changed = True
    return changed


def _migrate_revisions(conn: Any) -> None:
    # #211 M2: fresh databases run the post-v70 schema shape — the key column
    # is gone and the (empty) backfill is a no-op either way.
    if not has_column(conn, "workflow_revisions", "workflow_key"):
        return
    for revision in conn.execute(_ACTIVE_REVISIONS).fetchall():
        try:
            payload = json.loads(str(revision["definition_json"]))
        except json.JSONDecodeError:
            logger.warning(
                "active revision %s has corrupt definition_json; node-type backfill skipped",
                revision["id"],
            )
            continue
        if not isinstance(payload, dict):
            continue
        agent_nodes = _agent_route_nodes(
            conn, str(revision["workspace_id"]), str(revision["workflow_key"])
        )
        if not _backfill_revision_payload(payload, agent_nodes):
            continue
        # The publish rule: node_code_pins ride alongside the definition but
        # stay out of the hash.
        pure_payload = {key: value for key, value in payload.items() if key != "node_code_pins"}
        pure_json = _canonical_json(pure_payload)
        conn.execute(
            "update workflow_revisions set definition_json=%s, definition_hash=%s where id=%s",
            (
                _canonical_json(payload),
                hashlib.sha256(pure_json.encode("utf-8")).hexdigest(),
                revision["id"],
            ),
        )


def _migrate_drafts(conn: Any) -> None:
    if conn.execute(_DRAFTS_TABLE_PRESENT).fetchone()["oid"] is None:
        return
    for draft in conn.execute(_DRAFTS).fetchall():
        try:
            payload = yaml.safe_load(str(draft["definition_yaml"]))
        except yaml.YAMLError:
            logger.warning(
                "workspace %s workflow draft is not valid YAML; node-type backfill skipped",
                draft["workspace_id"],
            )
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), dict):
            continue
        agent_nodes = {
            str(row["node_key"])
            for row in conn.execute(_WORKSPACE_AGENT_ROUTE_NODES, (str(draft["workspace_id"]),))
        }
        changed = False
        for node_key, node in payload["nodes"].items():
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if node_type in _TYPED_VALUES:
                continue
            node["type"] = "agent" if str(node_key) in agent_nodes else "code"
            changed = True
        if not changed:
            continue
        conn.execute(
            "update workspace_workflow_drafts set definition_yaml=%s where workspace_id=%s",
            (
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                str(draft["workspace_id"]),
            ),
        )


def migrate_workflow_node_explicit_types(conn: Any) -> None:
    """Backfill explicit node types into active revisions and Studio drafts."""
    _migrate_revisions(conn)
    _migrate_drafts(conn)
