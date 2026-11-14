"""Schema v105: materialize stored workflow execution defaults (#805).

Only active revisions and Studio drafts are rewritten. The four legacy
workflow-level defaults (runtime/provider/model/thinking) fill empty fields on
explicit Agent nodes; node overrides, prompt fields, explicit non-Agent raw
nodes, pins and Agent provenance stay unchanged. Active revisions get a pure
definition hash (pins/provenance excluded), while successfully rewritten
drafts advance the draft generation in the same transaction.

Every source is prepared and semantically checked before any source update is
applied. Active errors abort the whole migration. Invalid or unsafe drafts are
backed up and reported as unresolved without blocking active revisions. Jobs,
archived revisions, queued manifests and runtime history are never read or
written. Replaying is a no-op because migrated sources no longer carry the
workflow-level key and unresolved drafts already have a backup.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import yaml

from server.app.workflows.schema import WorkflowDefinition, WorkflowDefinitionError

logger = logging.getLogger(__name__)

_DEFAULT_FIELDS = ("runtime", "provider", "model", "thinking")
_ALLOWED_FIELDS = frozenset((*_DEFAULT_FIELDS, "prompt", "prompt_mode"))
_HASH_EXCLUDED_KEYS = ("node_code_pins", "agent_profile_provenance")
_BACKUP_DDL = """
create table if not exists workflow_execution_retirement_backups (
  id bigserial primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  source text not null check(source in ('active_revision', 'draft')),
  source_id text not null,
  original_text text not null,
  original_hash text,
  report_json text not null,
  created_at timestamptz not null default current_timestamp,
  unique(source, source_id)
)
"""
_ACTIVE_REVISIONS = (
    "select id, workspace_id, definition_json, definition_hash from workflow_revisions"
    " where status='active' order by workspace_id, id for update"
)
_DRAFTS = (
    "select workspace_id, definition_yaml, updated_at from workspace_workflow_drafts"
    " order by workspace_id for update"
)


@dataclass(frozen=True)
class _Backup:
    workspace_id: str
    source: str
    source_id: str
    original_text: str
    original_hash: str | None
    report: dict[str, Any]


@dataclass(frozen=True)
class _ActiveUpdate:
    revision_id: str
    original_text: str
    original_hash: str
    definition_json: str
    definition_hash: str


@dataclass(frozen=True)
class _DraftUpdate:
    workspace_id: str
    original_text: str
    original_updated_at: Any
    definition_yaml: str


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pure_hash(payload: dict[str, Any]) -> str:
    pure = {key: value for key, value in payload.items() if key not in _HASH_EXCLUDED_KEYS}
    return hashlib.sha256(_canonical_json(pure).encode("utf-8")).hexdigest()


def _unsafe_execution_reason(payload: dict[str, Any]) -> str:
    execution = payload.get("execution")
    if execution is None:
        return ""
    if not isinstance(execution, dict):
        return "workflow execution must be a mapping or null"
    unknown = sorted(set(execution) - _ALLOWED_FIELDS)
    if unknown:
        return f"unknown workflow execution keys: {', '.join(map(str, unknown))}"
    for field_name in _ALLOWED_FIELDS:
        value = execution.get(field_name, "")
        if not isinstance(value, str):
            return f"workflow execution.{field_name} must be a string"
    for field_name in ("prompt", "prompt_mode"):
        if execution.get(field_name):
            return f"workflow execution.{field_name} is node-level only"
    return ""


def _load_legacy(payload: dict[str, Any]) -> WorkflowDefinition:
    """Load either stored spelling through the explicit legacy materializer."""
    from server.app.workflows.loader import workflow_definition_from_dict

    return workflow_definition_from_dict(copy.deepcopy(payload))


def _agent_executions(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        key: node.execution for key, node in definition.nodes.items() if node.node_type == "agent"
    }


def _materialize(
    payload: dict[str, Any], *, type_field: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate = copy.deepcopy(payload)
    defaults = candidate.pop("execution", None) or {}
    report: list[dict[str, Any]] = []
    for node_key, original_node in payload["nodes"].items():
        if not isinstance(original_node, dict) or original_node.get(type_field) != "agent":
            continue
        node = candidate["nodes"][node_key]
        original_execution = original_node.get("execution") or {}
        execution = dict(original_execution)
        materialized: list[str] = []
        for field_name in _DEFAULT_FIELDS:
            if not execution.get(field_name) and defaults.get(field_name):
                execution[field_name] = defaults[field_name]
                materialized.append(field_name)
        if materialized:
            node["execution"] = execution
        effective_runtime = execution.get("runtime") or ""
        status = "materialized" if materialized else "unchanged"
        if not effective_runtime:
            status = "legacy_without_runtime"
        report.append(
            {
                "node_key": node_key,
                "status": status,
                "materialized_fields": materialized,
                "preserved_overrides": [
                    field_name
                    for field_name in _DEFAULT_FIELDS
                    if original_execution.get(field_name)
                ],
            }
        )
    for node_key, original_node in payload["nodes"].items():
        if (
            not isinstance(original_node, dict) or original_node.get(type_field) != "agent"
        ) and candidate["nodes"][node_key] != original_node:
            raise ValueError(f"non-Agent raw node changed: {node_key}")
    return candidate, report


def _prepare_payload(
    payload: dict[str, Any], *, type_field: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unsafe = _unsafe_execution_reason(payload)
    if unsafe:
        raise ValueError(unsafe)
    before = _load_legacy(payload)
    candidate, report = _materialize(payload, type_field=type_field)
    after = _load_legacy(candidate)
    if _agent_executions(before) != _agent_executions(after):
        raise ValueError("Agent effective execution changed during materialization")
    return candidate, report


def _unresolved_backup(workspace_id: str, original: str, reason: str) -> _Backup:
    return _Backup(
        workspace_id=workspace_id,
        source="draft",
        source_id=workspace_id,
        original_text=original,
        original_hash=None,
        report={"status": "unresolved", "reason": reason, "warning": "draft left unchanged"},
    )


def _prepare_active(
    rows: list[Any],
) -> tuple[list[_Backup], list[_ActiveUpdate]]:
    backups: list[_Backup] = []
    updates: list[_ActiveUpdate] = []
    for row in rows:
        original = str(row["definition_json"])
        payload = json.loads(original)
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), dict):
            raise ValueError(f"active revision {row['id']} has an invalid definition shape")
        if "execution" not in payload:
            continue
        candidate, nodes = _prepare_payload(payload, type_field="node_type")
        workspace_id = str(row["workspace_id"])
        revision_id = str(row["id"])
        original_hash = str(row["definition_hash"])
        backups.append(
            _Backup(
                workspace_id,
                "active_revision",
                revision_id,
                original,
                original_hash,
                {"status": "migrated", "nodes": nodes},
            )
        )
        updates.append(
            _ActiveUpdate(
                revision_id,
                original,
                original_hash,
                _canonical_json(candidate),
                _pure_hash(candidate),
            )
        )
    return backups, updates


def _prepare_drafts(rows: list[Any]) -> tuple[list[_Backup], list[_DraftUpdate]]:
    backups: list[_Backup] = []
    updates: list[_DraftUpdate] = []
    for row in rows:
        workspace_id = str(row["workspace_id"])
        original = str(row["definition_yaml"])
        try:
            payload = yaml.safe_load(original)
        except yaml.YAMLError as exc:
            backups.append(_unresolved_backup(workspace_id, original, f"invalid_yaml: {exc}"))
            continue
        if not isinstance(payload, dict):
            backups.append(_unresolved_backup(workspace_id, original, "draft is not a mapping"))
            continue
        if "execution" not in payload:
            continue
        if not isinstance(payload.get("nodes"), dict):
            backups.append(
                _unresolved_backup(workspace_id, original, "draft nodes are not a mapping")
            )
            continue
        try:
            candidate, nodes = _prepare_payload(payload, type_field="type")
        except (WorkflowDefinitionError, ValueError, TypeError, AttributeError) as exc:
            backups.append(_unresolved_backup(workspace_id, original, str(exc)))
            continue
        backups.append(
            _Backup(
                workspace_id,
                "draft",
                workspace_id,
                original,
                None,
                {"status": "migrated", "nodes": nodes},
            )
        )
        updates.append(
            _DraftUpdate(
                workspace_id,
                original,
                row["updated_at"],
                yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False),
            )
        )
    return backups, updates


def _insert_backup(conn: Any, backup: _Backup) -> None:
    conn.execute(
        "insert into workflow_execution_retirement_backups"
        "(workspace_id, source, source_id, original_text, original_hash, report_json)"
        " values (%s, %s, %s, %s, %s, %s) on conflict(source, source_id) do nothing",
        (
            backup.workspace_id,
            backup.source,
            backup.source_id,
            backup.original_text,
            backup.original_hash,
            _canonical_json(backup.report),
        ),
    )


def _apply_active(conn: Any, update: _ActiveUpdate) -> None:
    cursor = conn.execute(
        "update workflow_revisions set definition_json=%s, definition_hash=%s"
        " where id=%s and status='active' and definition_json=%s and definition_hash=%s",
        (
            update.definition_json,
            update.definition_hash,
            update.revision_id,
            update.original_text,
            update.original_hash,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"active revision changed during v105 migration: {update.revision_id}")


def _apply_draft(conn: Any, update: _DraftUpdate) -> None:
    cursor = conn.execute(
        "update workspace_workflow_drafts set definition_yaml=%s, updated_at=current_timestamp"
        " where workspace_id=%s and definition_yaml=%s and updated_at=%s",
        (
            update.definition_yaml,
            update.workspace_id,
            update.original_text,
            update.original_updated_at,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"draft changed during v105 migration: {update.workspace_id}")
    conn.execute(
        "insert into workspace_workflow_draft_generation(workspace_id, generation) values (%s, 1)"
        " on conflict(workspace_id) do update set generation="
        "workspace_workflow_draft_generation.generation + 1",
        (update.workspace_id,),
    )


def migrate_workflow_execution_defaults_retirement(conn: Any) -> None:
    """Materialize legacy defaults atomically, retaining unresolved draft backups."""
    conn.execute(_BACKUP_DDL)
    active_backups, active_updates = _prepare_active(conn.execute(_ACTIVE_REVISIONS).fetchall())
    draft_backups, draft_updates = _prepare_drafts(conn.execute(_DRAFTS).fetchall())
    for backup in (*active_backups, *draft_backups):
        _insert_backup(conn, backup)
    for active_update in active_updates:
        _apply_active(conn, active_update)
    for draft_update in draft_updates:
        _apply_draft(conn, draft_update)
    unresolved = sum(backup.report["status"] == "unresolved" for backup in draft_backups)
    if unresolved:
        logger.warning(
            "v105: %s draft(s) left unchanged with unresolved execution defaults;"
            " see workflow_execution_retirement_backups.report_json",
            unresolved,
        )
