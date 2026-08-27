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

Rename mechanics: FKs are ``on delete cascade`` without ``on update
cascade``, and ``session_replication_role`` is a privileged setting
(superuser / explicit grant; the runbook's minimum privilege contract is
plain CREATE/USAGE), so the swap is privilege-free instead: insert a row
with the new id (copying every column), point every referencing row at the
new id, then delete the old row. The jobs/job_nodes status-count triggers
are USER triggers and disabled around the rewrite (table ownership only —
system/FK triggers stay active) so they don't double-count into the new
key. ``auth_scoped_tokens`` and ``ops_metric_samples`` carry
``workspace_id`` with no FK at all and are rewritten by hand (a missed
rename would orphan live security credentials);
``agent_workers.allowed_workspaces_json`` is a JSON scope list (also
FK-less) and is rewritten so registered workers keep claiming in the
renamed workspace.

Pre-checks fail fast (the transaction aborts, nothing is renamed): a target
id already used by another workspace, and a legacy key that violates the
v61 id contract (``^[a-z0-9][a-z0-9_-]{0,63}$`` — legacy keys were free-form
text, e.g. ``team/flow`` would produce an id the URL-addressed API could
never resolve). The operator renames the offending workspace, then re-runs.

Deliberately NOT rewritten: ``jobs.storage_dir`` paths on disk
(``data/jobs/<workspace_id>/``), material S3 ``storage_key`` prefixes, and
composite string ids (``run_id``, ``workflow_revisions.id``,
``workspace_node_routes`` capacity keys) — all are stored per row and parsed
in isolation, so legacy rows keep resolving under their old paths.

Idempotent on replay: a second run finds no row where id <> key and no empty
key to backfill, so every step becomes a no-op.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# v61 id contract (mirrors WorkspaceCreateRequest.id's pattern).
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# workspace_id columns WITHOUT a foreign key to workspaces(id). The FK'd
# children are derived from information_schema at run time; these are
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


def _preflight(conn: Any, renames: dict[str, str]) -> None:
    """Fail fast on id collisions and legacy keys violating the v61 contract."""
    all_ids = {str(row["id"]) for row in conn.execute("select id from workspaces").fetchall()}
    conflicts = [
        f"{old} -> {target}"
        for old, target in renames.items()
        if target in all_ids and target != old
    ]
    if conflicts:
        raise RuntimeError(
            "schema v61 workspace rename conflicts (target id already in use): "
            + "; ".join(conflicts)
            + ". Rename or delete the colliding workspace, then re-run."
        )
    invalid = [target for target in renames.values() if not _ID_PATTERN.match(target)]
    if invalid:
        raise RuntimeError(
            "schema v61: legacy workflow keys that violate the new id contract "
            f"({'[a-z0-9][a-z0-9_-]{{0,63}}'}): {sorted(invalid)}. Set the workspace's "
            "default_workflow_key to a valid id (e.g. via SQL), then re-run."
        )


def _rewrite_worker_scopes(conn: Any, old_id: str, target: str) -> None:
    """Rewrite agent_workers.allowed_workspaces_json entries old_id -> target."""
    rows = conn.execute(
        "select worker_id, allowed_workspaces_json from agent_workers"
        " where allowed_workspaces_json::jsonb @> jsonb_build_array(%s::text)",
        (old_id,),
    ).fetchall()
    for row in rows:
        scopes = json.loads(row["allowed_workspaces_json"] or "[]")
        rewritten = [target if entry == old_id else entry for entry in scopes]
        conn.execute(
            "update agent_workers set allowed_workspaces_json=%s where worker_id=%s",
            (json.dumps(rewritten), row["worker_id"]),
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

    _preflight(conn, renames)

    tables = _child_tables(conn) + list(_UNCONSTRAINED_WORKSPACE_ID_TABLES)
    # The jobs/job_nodes status-count triggers maintain the count rows per
    # workspace_id; rewriting jobs.workspace_id under those triggers would
    # double-count into the new key (PK collisions with the manually
    # rewritten count rows). Disabling USER triggers needs only table
    # ownership (system/FK triggers stay active); count rows are rewritten
    # by the migration itself and resync on the next jobs mutation.
    _TRIGGER_MAINTAINED_COUNT_TABLES = ("jobs", "job_nodes")
    for table in _TRIGGER_MAINTAINED_COUNT_TABLES:
        conn.execute(f"alter table {table} disable trigger user")
    try:
        for old_id, target in renames.items():
            # Privilege-free rename: the new-id parent row exists before any
            # child points at it (FK stays satisfied throughout), the old row
            # goes away only after nothing references it.
            conn.execute(
                "insert into workspaces (id, name, description, default_workflow_key,"
                " resource_config_json, node_config_json, default_entity,"
                " intake_config_json, created_at, updated_at, default_agent_provider,"
                " default_agent_model, default_agent_thinking)"
                " select %s, name, description, default_workflow_key,"
                " resource_config_json, node_config_json, default_entity, intake_config_json,"
                " created_at, updated_at, default_agent_provider, default_agent_model,"
                " default_agent_thinking from workspaces where id = %s",
                (target, old_id),
            )
            for table in tables:
                conn.execute(
                    f"update {table} set workspace_id=%s where workspace_id=%s",
                    (target, old_id),
                )
            _rewrite_worker_scopes(conn, old_id, target)
            conn.execute("delete from workspaces where id=%s", (old_id,))
            logger.info("schema v61: workspace %r renamed to %r", old_id, target)
    finally:
        for table in _TRIGGER_MAINTAINED_COUNT_TABLES:
            conn.execute(f"alter table {table} enable trigger user")

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
