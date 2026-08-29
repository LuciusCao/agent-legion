"""Schema v64 data migration: backfill workflow top-level execution defaults.

The retired ``workspaces.default_agent_*`` columns were the execution-config
default source; their workflow-scoped replacement is the top-level
``execution`` block of the active revision. Workspaces that still carry
non-empty defaults would silently lose them when the post-chain cleanup drops
the columns, so this migration (running inside the migration loop, while the
columns still exist) writes the non-empty defaults into the active revision's
``definition_json`` top-level ``execution`` — but only when the definition
does not already declare one (an explicit block always wins).

Idempotent: after the backfill the definition has a non-empty top-level
``execution``, so a replay skips it; a fresh database has no workspaces and
is a natural no-op. The source columns are probed first — a replay against
a database whose post-chain cleanup already dropped them (simulated upgrade
paths rebuild such shapes) is a no-op instead of a column error.
``definition_hash`` is recomputed alongside ``definition_json`` (the
executor_retirement precedent); in-flight jobs keep their own intake-frozen
snapshots and are untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_WORKSPACES_WITH_DEFAULTS = (
    "select id, default_workflow_key, default_agent_provider, default_agent_model,"
    " default_agent_thinking from workspaces"
    " where default_agent_provider <> '' or default_agent_model <> ''"
    " or default_agent_thinking <> ''"
)
_ACTIVE_REVISION = (
    "select id, definition_json from workflow_revisions"
    " where workspace_id=%s and workflow_key=%s and status='active'"
)
_DEFAULT_COLUMNS_PRESENT = (
    "select count(*) as n from information_schema.columns"
    " where table_schema=current_schema() and table_name='workspaces'"
    " and column_name='default_agent_provider'"
)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _has_top_level_execution(payload: dict[str, Any]) -> bool:
    execution = payload.get("execution")
    if not isinstance(execution, dict):
        return False
    return any(
        str(execution.get(field_name) or "") for field_name in ("provider", "model", "thinking")
    )


def migrate_workspace_execution_defaults(conn: Any) -> None:
    """Copy retired workspace Agent defaults into the active revision."""
    if int(conn.execute(_DEFAULT_COLUMNS_PRESENT).fetchone()["n"]) == 0:
        return  # Post-chain cleanup already dropped the source columns.
    for workspace in conn.execute(_WORKSPACES_WITH_DEFAULTS).fetchall():
        defaults = {
            "provider": str(workspace["default_agent_provider"] or ""),
            "model": str(workspace["default_agent_model"] or ""),
            "thinking": str(workspace["default_agent_thinking"] or ""),
        }
        revision = conn.execute(
            _ACTIVE_REVISION,
            (str(workspace["id"]), str(workspace["default_workflow_key"] or "")),
        ).fetchone()
        if revision is None:
            logger.warning(
                "workspace %s carries Agent defaults but has no active revision;"
                " defaults dropped without backfill",
                workspace["id"],
            )
            continue
        try:
            payload = json.loads(str(revision["definition_json"]))
        except json.JSONDecodeError:
            logger.warning(
                "workspace %s active revision %s has corrupt definition_json; skipped",
                workspace["id"],
                revision["id"],
            )
            continue
        if not isinstance(payload, dict) or _has_top_level_execution(payload):
            continue
        payload["execution"] = {**defaults, "prompt": ""}
        definition_json = _canonical_json(payload)
        conn.execute(
            "update workflow_revisions set definition_json=%s, definition_hash=%s where id=%s",
            (
                definition_json,
                hashlib.sha256(definition_json.encode("utf-8")).hexdigest(),
                revision["id"],
            ),
        )
