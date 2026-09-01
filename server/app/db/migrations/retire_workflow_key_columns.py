"""Schema v70: retire the workflow_key columns (#211 Phase 3 M2).

v68 aligned every stored key with the workspace id; M1 removed the runtime
predicates and normalized the write paths. What remains is DDL:

- The three composite-PK state tables (workspace_node_limits/_routes/
  _capacities) rebuild their primary keys without the key column; the
  v68-aligned rows make the narrowed key collision-free.
- workspace_job_node_status_counts narrows its PK the same way.
- jobs.workflow_key drops together with its six key-leading/key-member
  indexes; the workspace-keyed twins (v67/v69) and the new
  (workspace_id, status) / (workspace_id, source_type, source_id) twins
  cover every surviving predicate.
- runs / executor_leases / agent_execution_requests /
  workflow_revisions drop the column too (their twins or surviving
  predicates carry the load). The revision UNIQUE(workspace_id, version)
  and the two same-name indexes (idx_workflow_revisions_active,
  idx_agent_requests_node_active) are recreated explicitly: on upgraded
  databases the schema replay skips existing names and the column drop
  auto-drops the old-shape objects (Codex P1/P2 #334).

The trigger chain rewrites in the schema file (bump/sync/deduct/rekey lose
the workflow_key parameter; the rekey trigger narrows to workspace_id
updates). workspaces.default_workflow_key itself is M3 — the deprecation
window for old clients closes 2026-10-31.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Tables dropping the column outright (no PK change on them).
_DROP_COLUMN_TABLES = (
    "runs",
    "jobs",
    "executor_leases",
    "agent_execution_requests",
    "workflow_revisions",
)

# Composite-PK tables rebuilt without the key column: (node_key) narrowing
# for the three state tables, (node_key, status) for the count table.
_PK_TABLES = (
    ("workspace_node_limits", "(workspace_id, node_key)"),
    ("workspace_node_routes", "(workspace_id, node_key)"),
    ("workspace_node_capacities", "(workspace_id, node_key)"),
    ("workspace_job_node_status_counts", "(workspace_id, node_key, status)"),
)

_RETIRED_INDEXES = (
    "idx_jobs_workflow_status",
    "idx_jobs_workflow_updated",
    "idx_jobs_active_marks",
    "idx_jobs_workflow_source",
    "idx_jobs_workspace_workflow_status",
    "idx_jobs_workspace_workflow_source",
    "idx_executor_leases_workflow_node_active",
)


# Same-name redefinitions: the schema replay cannot apply them on upgraded
# databases (``create ... if not exists`` skips names the old shape already
# claims), and the column drop then auto-drops the old-shape objects — so
# this migration must recreate them explicitly. Fresh databases already
# carry the terminal shapes; the if-not-exists / drop-then-add forms below
# stay no-ops there.
_RECREATED_INDEXES = (
    "create index if not exists idx_workflow_revisions_active"
    " on workflow_revisions(workspace_id, status)",
    "create index if not exists idx_agent_requests_node_active"
    " on agent_execution_requests(workspace_id, node_key, state)",
)
_REVISIONS_UNIQUE = "workflow_revisions_workspace_id_version_key"


def has_column(conn: Any, table: str, column: str) -> bool:
    """Probe whether a column survives in the current schema shape.

    Fresh databases replay ``postgres_schema.sql`` (the terminal v70 shape)
    before any data migration runs, so historical migrations meeting the
    post-v70 shape must skip their workflow_key work instead of failing.
    Kept here — the v70 module owns the retirement the probes guard.
    """
    row = conn.execute(
        "select 1 from information_schema.columns"
        " where table_schema=current_schema() and table_name=%s"
        " and column_name=%s",
        (table, column),
    ).fetchone()
    return row is not None


def migrate_retire_workflow_key_columns(conn: Any) -> None:
    """Drop the retired columns, rebuild the narrowed PKs, swap indexes."""
    for index in _RETIRED_INDEXES:
        conn.execute(f"drop index if exists {index}")
    for table, pk in _PK_TABLES:
        conn.execute(f"alter table {table} drop constraint if exists {table}_pkey")
        conn.execute(f"alter table {table} drop column if exists workflow_key")
        conn.execute(f"alter table {table} add primary key {pk}")
    for table in _DROP_COLUMN_TABLES:
        conn.execute(f"alter table {table} drop column if exists workflow_key")
    # The revision unique constraint auto-dropped with its column (the old
    # UNIQUE(workspace_id, workflow_key, version)); the narrowed terminal
    # shape is recreated here — upgraded databases must reject duplicate
    # (workspace_id, version) rows exactly like fresh ones (Codex P1 #334).
    conn.execute(f"alter table workflow_revisions drop constraint if exists {_REVISIONS_UNIQUE}")
    conn.execute(
        f"alter table workflow_revisions add constraint {_REVISIONS_UNIQUE}"
        " unique (workspace_id, version)"
    )
    # Same-name indexes the old shape claimed: the schema replay skipped
    # them and the column drop auto-dropped them (Codex P2 #334).
    for statement in _RECREATED_INDEXES:
        conn.execute(statement)
    logger.info(
        "schema v70: retired workflow_key columns from %d table(s)",
        len(_PK_TABLES) + len(_DROP_COLUMN_TABLES),
    )
