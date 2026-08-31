"""Schema v68: align every stored workflow_key with the v62 binding (#211 Phase 3).

The v62 rename cascades workspace_id but leaves workflow_key columns as
stored data; the Phase 3 read-layer binding drops their predicates, so stale
pre-rename values would break lookups (job scan resurrecting old rows under
the current revision's DAG — Codex P1 #313; replay routing and node-code
resolution missing old-key rows — Codex P1 #315). This migration rewrites
every live table's workflow_key to the workspace id; each surface's
collision rule lives as the inline comment where it executes:

- Plain column tables (runs, jobs, executor_leases, agent_execution_requests):
  plain UPDATE; jobs' rekey trigger moves the per-node counts along.
- Composite-key surfaces (the three state tables, status counts,
  workflow_revisions): the window-era workspace-id row is the live truth, so
  old-key twins drop or merge BEFORE the rewrite (a plain UPDATE would
  collide mid-flight); revision history shifts past the workspace's version
  maximum.
- versioned_entities.entity_key: node_code rows re-prefix with the workspace
  id, published-wins on collisions (dispatch resolves published code; frozen
  pins reference published rows). Agent rows key on agent_id — untouched.

NOT rewritten: composite ids (run_id/job_id/revision_id) and storage_dir
paths embed the old key as opaque text (v62 decisions); archaeology tables
(workspace_node_bindings, job_batches, workflow_node_codes) only feed
historical migration replays.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.db.migrations.retire_workflow_key_columns import has_column

logger = logging.getLogger(__name__)

_COLUMN_TABLES = (
    "runs",
    "jobs",
    "executor_leases",
    "agent_execution_requests",
)

# Composite-unique state tables: the (workspace_id, node_key) row already
# written under the identity value during the v62→v68 window is the live
# truth; the old-key twin drops.
_STATE_TABLES = (
    "workspace_node_routes",
    "workspace_node_limits",
    "workspace_node_capacities",
)


def _rewrite_column_tables(conn: Any) -> int:
    total = 0
    for table in _COLUMN_TABLES:
        # #211 M2: fresh databases already run the post-v70 shape (column
        # gone); each pass skips those tables instead of failing the replay.
        if not has_column(conn, table, "workflow_key"):
            continue
        result = conn.execute(
            f"update {table} set workflow_key = workspace_id"
            " where workflow_key is distinct from workspace_id"
        )
        total += getattr(result, "rowcount", 0) or 0
    return total


def _drop_state_table_twins(conn: Any) -> int:
    total = 0
    for table in _STATE_TABLES:
        if not has_column(conn, table, "workflow_key"):
            continue
        # Window-era twin first (the live truth), then any further old-key
        # duplicates of one node (multi-generation keys): keep the newest
        # row per (workspace_id, node_key) so the rewrite cannot collide on
        # the composite PK.
        result = conn.execute(
            f"""
            delete from {table} as old
            using {table} as new
            where old.workflow_key is distinct from old.workspace_id
              and new.workspace_id = old.workspace_id
              and new.workflow_key = old.workspace_id
              and new.node_key = old.node_key
            """
        )
        total += getattr(result, "rowcount", 0) or 0
        # No timestamp column exists — the lexicographically largest key
        # wins (deterministic; any one row satisfies the composite PK).
        result = conn.execute(
            f"""
            delete from {table} as old
            using {table} as newer
            where old.workflow_key is distinct from old.workspace_id
              and newer.workspace_id = old.workspace_id
              and newer.workflow_key is distinct from newer.workspace_id
              and newer.node_key = old.node_key
              and newer.workflow_key > old.workflow_key
            """
        )
        total += getattr(result, "rowcount", 0) or 0
        result = conn.execute(
            f"update {table} set workflow_key = workspace_id"
            " where workflow_key is distinct from workspace_id"
        )
        total += getattr(result, "rowcount", 0) or 0
    return total


def _align_revisions(conn: Any) -> int:
    """Archive old-key actives, shift the old history past the workspace's
    overall version maximum, rewrite the key."""
    if not has_column(conn, "workflow_revisions", "workflow_key"):
        return 0
    # Archive an old-key active only when an identity-key active already
    # exists (the window-era republish); otherwise it is the workspace's
    # ONLY active and survives the rewrite (Codex P1 on #315).
    conn.execute(
        """
        update workflow_revisions as old
        set status = 'archived'
        from workflow_revisions as new
        where old.workflow_key is distinct from old.workspace_id
          and old.status = 'active'
          and new.workspace_id = old.workspace_id
          and new.workflow_key = old.workspace_id
          and new.status = 'active'
          and new.id is distinct from old.id
        """
    )
    shifted = conn.execute(
        """
        update workflow_revisions as old
        set version = old.version + shifted.max_version
        from (
          select workspace_id, max(version) as max_version
          from workflow_revisions
          group by workspace_id
        ) as shifted
        where old.workflow_key is distinct from old.workspace_id
          and old.workspace_id = shifted.workspace_id
        """
    )
    rewritten = conn.execute(
        "update workflow_revisions set workflow_key = workspace_id"
        " where workflow_key is distinct from workspace_id"
    )
    return (getattr(shifted, "rowcount", 0) or 0) + (getattr(rewritten, "rowcount", 0) or 0)


def _align_status_counts(conn: Any) -> int:
    """Merge old-key count rows into workspace-id rows, then drop them."""
    if not has_column(conn, "workspace_job_node_status_counts", "workflow_key"):
        return 0
    merged = conn.execute(
        """
        insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
        select c.workspace_id, c.workspace_id, c.node_key, c.status, c.cnt
        from workspace_job_node_status_counts c
        where c.workflow_key is distinct from c.workspace_id
        on conflict (workspace_id, workflow_key, node_key, status)
        do update set cnt = workspace_job_node_status_counts.cnt + excluded.cnt
        """
    )
    deleted = conn.execute(
        "delete from workspace_job_node_status_counts"
        " where workflow_key is distinct from workspace_id"
    )
    return (getattr(merged, "rowcount", 0) or 0) + (getattr(deleted, "rowcount", 0) or 0)


def _align_entity_keys(conn: Any) -> int:
    """Re-prefix node_code entity keys with the workspace id.

    Collision rules (same-version twins cannot renumber without breaking
    frozen pins): published wins — a non-published window-era twin drops
    behind an old-key published row, and a non-published old-key twin drops
    behind a window-era row. Superseded old-key published rows downgrade to
    archived first (the history survives for pinned replays).
    """
    # Old-key published beats a non-published window-era same-version twin.
    conn.execute(
        """
        delete from versioned_entities as new
        using versioned_entities as old
        where new.entity_type = 'node_code'
          and new.workspace_id is not null
          and new.status != 'published'
          and new.entity_key = new.workspace_id
              || substring(new.entity_key from position(':' in new.entity_key))
          and old.entity_type = 'node_code'
          and old.workspace_id = new.workspace_id
          and old.status = 'published'
          and old.entity_key is distinct from
              old.workspace_id || substring(old.entity_key from position(':' in old.entity_key))
          and old.version = new.version
          and old.id is distinct from new.id
        """
    )
    # Window-era row beats a non-published old-key same-version twin.
    conn.execute(
        """
        delete from versioned_entities as old
        using versioned_entities as new
        where old.entity_type = 'node_code'
          and old.workspace_id is not null
          and old.status != 'published'
          and old.entity_key is distinct from
              old.workspace_id || substring(old.entity_key from position(':' in old.entity_key))
          and new.entity_type = 'node_code'
          and new.workspace_id = old.workspace_id
          and new.entity_key = old.workspace_id
              || substring(old.entity_key from position(':' in old.entity_key))
          and new.version = old.version
          and new.id is distinct from old.id
        """
    )
    # Both-published same-version twins (subagent P1 on #315): the old-key
    # row drops — version numbers identify the CURRENT publish history of
    # the entity-key domain, and the window era restarted that history under
    # the identity key. A frozen pin referencing the old-key row falls back
    # to the revision snapshot's node_code_pins (version + hash ride the
    # definition_json), which is the audit contract since #115.
    conn.execute(
        """
        delete from versioned_entities as old
        using versioned_entities as new
        where old.entity_type = 'node_code'
          and old.workspace_id is not null
          and old.status = 'published'
          and old.entity_key is distinct from
              old.workspace_id || substring(old.entity_key from position(':' in old.entity_key))
          and new.entity_type = 'node_code'
          and new.workspace_id = old.workspace_id
          and new.status = 'published'
          and new.entity_key = old.workspace_id
              || substring(old.entity_key from position(':' in old.entity_key))
          and new.version = old.version
          and new.id is distinct from old.id
        """
    )
    # After the twin pass: an old-key published row that a window-era
    # publish superseded downgrades to archived — its same-version twin
    # (if any) is gone, so the demotion can no longer feed the twin-delete
    # and erase the exact hash a frozen pin references (Codex P1 on #315).
    conn.execute(
        """
        update versioned_entities as old
        set status = 'archived'
        from versioned_entities as new
        where old.entity_type = 'node_code'
          and old.workspace_id is not null
          and old.status = 'published'
          and old.entity_key is distinct from
              old.workspace_id || substring(old.entity_key from position(':' in old.entity_key))
          and new.entity_type = 'node_code'
          and new.workspace_id = old.workspace_id
          and new.status = 'published'
          and new.entity_key = old.workspace_id
              || substring(old.entity_key from position(':' in old.entity_key))
          and new.id is distinct from old.id
        """
    )
    # position() > 0 guards against separator-less keys (bare SQL only —
    # the service layer validates the separator); substring from 0 would
    # return the whole key and produce a concatenation, not a re-prefix.
    result = conn.execute(
        """
        update versioned_entities
        set entity_key = workspace_id
            || substring(entity_key from position(':' in entity_key))
        where entity_type = 'node_code'
          and workspace_id is not null
          and position(':' in entity_key) > 0
          and entity_key is distinct from
              workspace_id || substring(entity_key from position(':' in entity_key))
        """
    )
    return getattr(result, "rowcount", 0) or 0


def migrate_jobs_workflow_key_alignment(conn: Any) -> None:
    """Rewrite every stored workflow_key to the workspace id where they differ."""
    total = (
        _rewrite_column_tables(conn)
        + _drop_state_table_twins(conn)
        + _align_revisions(conn)
        + _align_status_counts(conn)
        + _align_entity_keys(conn)
    )
    if total:
        logger.info("schema v68: aligned workflow_key on %d row(s)", total)
