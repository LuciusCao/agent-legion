"""Schema v61: bind workspace id and workflow key (issue #211).

Workspaces are renamed so ``id == default_workflow_key`` (the key is bound at
creation from v61 on and immutable); never-published workspaces keep their id
and get the key backfilled from it. These tests pin the version record and
cover rename cascades (FK children plus the two unconstrained tables), empty
key backfill, conflict fail-fast, and idempotent replay.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str, key: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, %s) on conflict do nothing",
        (workspace_id, workspace_id, key),
    )


def test_schema_version_pin() -> None:
    # The latest-migration record pin moved through
    # test_retire_global_register_tokens_migration.py (v58) →
    # test_jobs_run_id_index.py (v59) → back to the v58 file for the DDL-only
    # v60; v61 owns its own module, so the pin lives here now.
    assert SCHEMA_VERSION == 61
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "workspace_id_key_binding"


def test_renames_ids_to_keys_and_cascades_children() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "bind-rename-ws", "bind_renamed_flow")
        _seed_workspace(conn, "bind-empty-ws", "")
        # One FK child row and one unconstrained-table row per workspace: the
        # rename must rewrite both (a missed auth_scoped_tokens row would
        # orphan a live credential).
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values ('bind-job-1', 'bind-rename-ws', 'bind_renamed_flow', 'material', 'm1')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into ops_metric_samples(bucket_start, workspace_id)"
            " values (current_timestamp, 'bind-rename-ws'),"
            " (current_timestamp, 'bind-empty-ws')"
        )

    from server.app.db.migrations.workspace_id_key_binding import (
        migrate_workspace_id_key_binding,
    )

    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_workspace_id_key_binding(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        ws = {
            row["id"]: row["default_workflow_key"]
            for row in conn.execute("select id, default_workflow_key from workspaces").fetchall()
        }
        job_ws = conn.execute("select workspace_id from jobs where id='bind-job-1'").fetchone()[
            "workspace_id"
        ]
        metric_ws = {
            row["workspace_id"]
            for row in conn.execute(
                "select distinct workspace_id from ops_metric_samples"
                " where workspace_id in ('bind_renamed_flow', 'bind-empty-ws')"
            ).fetchall()
        }
    # Renamed workspace: id now equals its key.
    assert "bind-rename-ws" not in ws
    assert ws["bind_renamed_flow"] == "bind_renamed_flow"
    # Never-published workspace: id kept, key backfilled from it.
    assert ws["bind-empty-ws"] == "bind-empty-ws"
    assert job_ws == "bind_renamed_flow"
    assert metric_ws == {"bind_renamed_flow", "bind-empty-ws"}


def test_conflicting_target_fails_fast() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        # bind-conflict-a wants id "shared_flow", which bind-conflict-b
        # already occupies: no automatic resolution is safe.
        _seed_workspace(conn, "bind-conflict-a", "shared_flow")
        _seed_workspace(conn, "shared_flow", "shared_flow")

    from server.app.db.migrations.workspace_id_key_binding import (
        migrate_workspace_id_key_binding,
    )

    with (
        pytest.raises(RuntimeError, match="bind-conflict-a -> shared_flow"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        migrate_workspace_id_key_binding(conn)

    # The failed transaction rolled back: neither workspace moved.
    with read_connection(TEST_DATABASE_URL) as conn:
        ids = {
            str(row["id"])
            for row in conn.execute(
                "select id from workspaces where id in ('bind-conflict-a', 'shared_flow')"
            ).fetchall()
        }
    assert ids == {"bind-conflict-a", "shared_flow"}


def test_replay_is_idempotent() -> None:
    from server.app.db.migrations.workspace_id_key_binding import (
        migrate_workspace_id_key_binding,
    )

    for _ in range(2):
        with write_transaction(TEST_DATABASE_URL) as conn:
            migrate_workspace_id_key_binding(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        bad = conn.execute(
            "select id from workspaces where id <> default_workflow_key"
            " or default_workflow_key = ''"
        ).fetchall()
    assert bad == [], "post-v61 invariant violated: every workspace has id == key"
