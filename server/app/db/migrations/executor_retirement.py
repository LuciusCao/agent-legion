"""Executor concept retirement (schema v47).

Harvests every published executor definition into the places that outlive
the concept, then drops the executor configuration surface:

1. Harvest: build capability → {config_schema, timeout_seconds,
   sandbox_network, global_capacity} from the published executor entities.
   For every workspace's ACTIVE workflow revision, each node whose
   capability hits the map — and has no published Agent in that workspace
   (an Agent schema wins over the node layer at dispatch) — gets the
   capability config_schema injected as the node-level ``config_schema``
   (P-0.5 step 1). Platform-reserved execution keys
   (``timeout_seconds``/``sandbox_network``) are STRIPPED from the injected
   schema — a node config_schema may not redeclare them — and their
   declared defaults land in the node ``config`` instead; non-default
   executor-level timeout/network values join the same config injection
   (existing node config keys always win).
2. code_capacity: the max global_capacity across code executors, merged
   into the ``global_settings`` instance document only when it differs from
   the code default (16); a pre-existing ``code_capacity`` key wins.
3. Drop ``workspace_executor_allocations`` / ``workspace_node_bindings``.
4. Delete every entity_type='executor' row and rebuild the entity_type
   CHECK without 'executor' (same drop + re-add as v30).

Orphan capabilities (no active-revision reference) and non-code executor
kinds log a warning and are not carried over. Idempotent on replay: the
harvest reads only executor rows (gone after one run), injections skip
nodes that already carry an identical declaration, and every DDL step is
if-exists/if-not-exists. Frozen constants only — migrations never import
the evolving service/executor code.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Frozen snapshots of the P-0.5 step-1 platform constants.
_RESERVED_KEYS = ("timeout_seconds", "sandbox_network")
_DEFAULT_TIMEOUT_SECONDS = 600
_DEFAULT_SANDBOX_NETWORK = False
_DEFAULT_CODE_CAPACITY = 16

_PUBLISHED_EXECUTORS = """
select entity_key, definition_json from versioned_entities
where entity_type='executor' and status='published' order by entity_key
"""

_ACTIVE_REVISIONS = """
select id, workspace_id, definition_json from workflow_revisions where status='active'
"""

_AGENT_CAPABILITIES = """
select definition_json::jsonb->>'capability' as capability from versioned_entities
where entity_type='agent' and workspace_id=%s and status='published'
"""

_DROP_ALLOCATIONS = "drop table if exists workspace_executor_allocations"
_DROP_BINDINGS = "drop table if exists workspace_node_bindings"
_DELETE_EXECUTOR_ENTITIES = "delete from versioned_entities where entity_type='executor'"
_REBUILD_ENTITY_TYPE_CHECK = """
alter table versioned_entities
  drop constraint if exists versioned_entities_entity_type_check;
alter table versioned_entities
  add constraint versioned_entities_entity_type_check
  check(entity_type in ('node_code', 'agent'))
"""


def _canon(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _harvest_map(conn: Any) -> dict[str, dict[str, Any]]:
    """capability → harvested executor declaration (first executor wins)."""
    harvested: dict[str, dict[str, Any]] = {}
    for row in conn.execute(_PUBLISHED_EXECUTORS).fetchall():
        definition = json.loads(str(row["definition_json"]))
        kind = definition.get("kind")
        if kind != "code":
            logger.warning(
                "executor retirement: non-code executor %r (kind=%r) is not carried over",
                row["entity_key"],
                kind,
            )
            continue
        capabilities = definition.get("capabilities")
        if not isinstance(capabilities, dict):
            continue
        for capability, cap in capabilities.items():
            if not capability or capability in harvested or not isinstance(cap, dict):
                continue
            harvested[str(capability)] = {
                "config_schema": cap.get("config_schema") or {},
                "timeout_seconds": cap.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS),
                "sandbox_network": cap.get("sandbox_network", _DEFAULT_SANDBOX_NETWORK),
                "global_capacity": definition.get("global_capacity"),
            }
    return harvested


def _split_reserved(schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(schema without reserved keys, reserved defaults as config values)."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return dict(schema), {}
    reserved_values: dict[str, Any] = {}
    stripped = {name: prop for name, prop in properties.items() if name not in _RESERVED_KEYS}
    for name in _RESERVED_KEYS:
        prop = properties.get(name)
        if isinstance(prop, dict) and "default" in prop:
            reserved_values[name] = prop["default"]
    result = dict(schema)
    result["properties"] = stripped
    return result, reserved_values


def _inject_node(node: dict[str, Any], entry: dict[str, Any], warnings: list[str]) -> bool:
    """Inject the harvested declaration into one revision node; True on change."""
    schema, reserved_values = _split_reserved(entry["config_schema"])
    changed = False
    if schema.get("properties"):
        existing = node.get("config_schema")
        if existing is None:
            node["config_schema"] = schema
            changed = True
        elif existing != schema:
            warnings.append(str(node.get("key") or "?"))
    config_values = dict(reserved_values)
    if entry["timeout_seconds"] != _DEFAULT_TIMEOUT_SECONDS:
        config_values.setdefault("timeout_seconds", entry["timeout_seconds"])
    if entry["sandbox_network"] != _DEFAULT_SANDBOX_NETWORK:
        config_values.setdefault("sandbox_network", entry["sandbox_network"])
    if config_values:
        config = node.get("config")
        if not isinstance(config, dict):
            config = {}
            node["config"] = config
        for key, value in config_values.items():
            if key not in config:
                config[key] = value
                changed = True
    return changed


def _harvest_revisions(conn: Any, harvested: dict[str, dict[str, Any]]) -> None:
    if not harvested:
        return
    agent_caps_cache: dict[str, set[str]] = {}
    referenced: set[str] = set()
    for row in conn.execute(_ACTIVE_REVISIONS).fetchall():
        workspace_id = str(row["workspace_id"])
        if workspace_id not in agent_caps_cache:
            agent_caps_cache[workspace_id] = {
                str(r["capability"])
                for r in conn.execute(_AGENT_CAPABILITIES, (workspace_id,)).fetchall()
                if r["capability"]
            }
        agent_capabilities = agent_caps_cache[workspace_id]
        payload = json.loads(str(row["definition_json"]))
        nodes = payload.get("nodes")
        if not isinstance(nodes, dict):
            continue
        changed = False
        mismatches: list[str] = []
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            capability = str(node.get("capability") or "")
            entry = harvested.get(capability)
            if entry is None:
                continue
            referenced.add(capability)
            if capability in agent_capabilities:
                continue
            changed = _inject_node(node, entry, mismatches) or changed
        for node_key in mismatches:
            logger.warning(
                "executor retirement: workspace %s revision %s node %r already declares a"
                " different config_schema; left untouched",
                workspace_id,
                row["id"],
                node_key,
            )
        if changed:
            definition_json = _canon(payload)
            conn.execute(
                "update workflow_revisions set definition_json=%s, definition_hash=%s where id=%s",
                (
                    definition_json,
                    hashlib.sha256(definition_json.encode("utf-8")).hexdigest(),
                    row["id"],
                ),
            )
    for capability in sorted(set(harvested) - referenced):
        logger.warning(
            "executor retirement: capability %r is not referenced by any active revision",
            capability,
        )


def _persist_code_capacity(conn: Any, harvested: dict[str, dict[str, Any]]) -> None:
    capacities = [
        int(entry["global_capacity"])
        for entry in harvested.values()
        if isinstance(entry["global_capacity"], int)
    ]
    if not capacities:
        return
    code_capacity = max(capacities)
    if code_capacity == _DEFAULT_CODE_CAPACITY:
        return
    row = conn.execute("select value from global_settings where key='instance'").fetchone()
    document = json.loads(str(row["value"])) if row is not None else {}
    if "code_capacity" in document:
        return
    document["code_capacity"] = code_capacity
    conn.execute(
        "insert into global_settings(key, value) values ('instance', %s)"
        " on conflict(key) do update set value=excluded.value, updated_at=current_timestamp",
        (json.dumps(document),),
    )


def migrate_executor_retirement(conn: Any) -> None:
    """Harvest executor declarations, then drop the executor surface (v47)."""
    harvested = _harvest_map(conn)
    _harvest_revisions(conn, harvested)
    _persist_code_capacity(conn, harvested)
    if conn.execute("select to_regclass('public.workspace_executor_allocations')").fetchone()[
        "to_regclass"
    ]:
        # One-time seed (schema v6), relocated from the schema-file replay:
        # the legacy per-workspace `pi` allocation was the de-facto Agent
        # limit before workspace_agent_capacities existed.
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
            " select workspace_id, concurrency_limit from workspace_executor_allocations"
            " where executor_id = 'pi' on conflict(workspace_id) do nothing"
        )
    conn.execute(_DROP_ALLOCATIONS)
    conn.execute(_DROP_BINDINGS)
    conn.execute(_DELETE_EXECUTOR_ENTITIES)
    conn.execute(_REBUILD_ENTITY_TYPE_CHECK)
