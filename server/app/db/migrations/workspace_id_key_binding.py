"""Workspace id / workflow key binding (schema v61, issue #211).

Schema v50 let blank-canvas workspaces start with an empty
``default_workflow_key``; the first publish then adopted the draft key. That
window is where a typo'd hand-typed key got permanently claimed, and it is
also why workspace stats 400'd for unpublished workspaces (build_workspace_stats
raises on an empty key).

v61 binds the two identifiers for good: a workspace's id IS its workflow key
(created together, immutable from then on). This migration renames existing
workspaces to their bound key — ``id = default_workflow_key`` — so the
invariant holds for legacy rows too. Workspaces with an empty key (never
published, e.g. blank-canvas ones like course_builder) keep their id and get
the key backfilled from it.

FK cascades: every ``workspace_id references workspaces(id)`` clause is ``on
delete cascade`` without ``on update cascade``, so the rename rewrites the
parent id and each child row explicitly. ``auth_scoped_tokens`` and
``ops_metric_samples`` carry ``workspace_id`` with no FK at all and are
included by hand (a missed rename would orphan live security credentials).

Deliberately NOT rewritten: ``jobs.storage_dir`` paths on disk
(``data/jobs/<workspace_id>/``), material S3 ``storage_key`` prefixes, and
composite string ids (``run_id``, ``workflow_revisions.id``,
``workspace_node_routes`` capacity keys) — all are stored per row and parsed
in isolation, so legacy rows keep resolving under their old paths.

Idempotent on replay: a second run finds no row where id <> key and no empty
key to backfill, so every step becomes a no-op.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# workspace_id columns WITHOUT a foreign key to workspaces(id). The FK'd
# children are derived from information_schema at run time; these two are
# maintained by hand because nothing in the catalog ties them to workspaces.
_UNCONSTRAINED_WORKSPACE_ID_TABLES = ("auth_scoped_tokens", "ops_metric_samples")


def _child_tables(conn: Any) -> list[str]:
    rows = conn.execute(
        """
        select distinct tc.table_name
        from information_schema.table_constraints tc
        join information_schema.constraint_column_usage ccu
          on tc.constraint_name = ccu.constraint_name
        where tc.constraint_type = 'FOREIGN KEY'
          and ccu.table_name = 'workspaces'
        order by tc.table_name
        """
    ).fetchall()
    return [str(row["table_name"]) for row in rows]


def _raise_on_id_conflicts(conn: Any, renames: dict[str, str]) -> None:
    """Fail fast when a target id is already taken by a different workspace.

    A conflict means two workspaces claim the same key (or a key collides
    with another workspace's id) — no automatic resolution is safe. The
    operator renames or deletes one side, then re-runs the migration.
    """
    all_ids = {str(row["id"]) for row in conn.execute("select id from workspaces").fetchall()}
    conflicts = []
    for old_id, target in renames.items():
        if target in all_ids and target != old_id:
            conflicts.append(f"{old_id} -> {target}")
    if conflicts:
        # The transaction aborts; the message lists every conflict so the
        # operator can resolve them in one pass instead of one per retry.
        raise RuntimeError(
            "schema v61 workspace rename conflicts (target id already in use): "
            + "; ".join(conflicts)
            + ". Rename or delete the colliding workspace, then re-run."
        )


def migrate_workspace_id_key_binding(conn: Any) -> None:
    """Bind workspace id and workflow key: rename ids to their keys (v61)."""
    rows = conn.execute("select id, default_workflow_key from workspaces").fetchall()
    renames: dict[str, str] = {}
    for row in rows:
        old_id = str(row["id"])
        key = str(row["default_workflow_key"] or "")
        if key and key != old_id:
            renames[old_id] = key
    if not renames and not any(not str(row["default_workflow_key"] or "") for row in rows):
        return

    _raise_on_id_conflicts(conn, renames)

    tables = _child_tables(conn) + list(_UNCONSTRAINED_WORKSPACE_ID_TABLES)
    # FKs are on-delete-cascade only (no on-update), so each rename updates
    # the parent row and every child's workspace_id explicitly. Switching the
    # session to replica role suspends trigger-based FK enforcement for the
    # duration of the swap (the test user owns its tables but is not
    # superuser, so per-table DISABLE TRIGGER would not apply); the enclosing
    # migration transaction keeps the swap atomic and the final verify query
    # catches any row a hand-maintained table list missed.
    conn.execute("set local session_replication_role = replica")
    try:
        for old_id, target in renames.items():
            conn.execute("update workspaces set id=%s where id=%s", (target, old_id))
            for table in tables:
                conn.execute(
                    f"update {table} set workspace_id=%s where workspace_id=%s",
                    (target, old_id),
                )
            logger.info("schema v61: workspace %r renamed to %r", old_id, target)
    finally:
        conn.execute("set local session_replication_role = default")

    # Never-published workspaces (empty key): backfill the key from the id so
    # the id==key invariant holds uniformly.
    conn.execute("update workspaces set default_workflow_key = id where default_workflow_key = ''")
    # Verify no orphaned workspace_id rows remain: every child reference must
    # point at an existing workspace (a hand-maintained table list that missed
    # a table would fail here instead of silently orphaning rows).
    for table in tables:
        orphans = conn.execute(
            f"select count(*) as n from {table} t"
            " where t.workspace_id <> '' and not exists"
            " (select 1 from workspaces w where w.id = t.workspace_id)"
        ).fetchone()["n"]
        if orphans:
            raise RuntimeError(f"schema v61 rename left {orphans} orphaned rows in {table}")
