"""Schema v62: bind workspace id and workflow key (issue #211).

Workspaces are renamed so ``id == default_workflow_key`` (the key is bound at
creation from v62 on and immutable); never-published workspaces keep their id
and get the key backfilled from it. These tests pin the version record and
cover rename cascades (FK children plus the two unconstrained tables), empty
key backfill, conflict and shared-key fail-fast, and idempotent replay.
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
    # v60; v62 owns its own module, and v63 is cleanup-only (the retired
    # default_agent_* columns drop in schema.py's post-chain sweep, no
    # migration module), so the pin stays here.
    assert SCHEMA_VERSION == 63
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "workspace_settings_retirement"


def test_renames_ids_to_keys_and_cascades_children() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        # Direct-call on a v63 database: the retired default_agent_* columns
        # are already dropped by the post-chain cleanup, but the v62
        # migration's insert still references them (they exist on every real
        # pre-v63 database) — restore the pre-v63 shape first.
        for column in (
            "default_agent_provider",
            "default_agent_model",
            "default_agent_thinking",
        ):
            conn.execute(
                f"alter table workspaces add column if not exists {column} text not null default ''"
            )
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
        # A registered worker scoped to the old id: the JSON scope list has no
        # FK, so the migration must rewrite it or the worker stops claiming.
        conn.execute(
            "insert into agent_workers(worker_id, name, runtimes_json, capabilities_json,"
            " models_json, max_concurrency, token_hash, allowed_workspaces_json,"
            " protocol_version, registered_at, last_seen_at)"
            " values ('bind-worker-1', 'Bind Worker', '[]', '[]', '[]', 4, 'x',"
            " '[\"bind-rename-ws\"]', 1, current_timestamp, current_timestamp)"
            " on conflict do nothing"
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
        worker_scope = conn.execute(
            "select allowed_workspaces_json from agent_workers where worker_id='bind-worker-1'"
        ).fetchone()["allowed_workspaces_json"]
    # Renamed workspace: id now equals its key.
    assert "bind-rename-ws" not in ws
    assert ws["bind_renamed_flow"] == "bind_renamed_flow"
    # Never-published workspace: id kept, key backfilled from it.
    assert ws["bind-empty-ws"] == "bind-empty-ws"
    assert job_ws == "bind_renamed_flow"
    assert metric_ws == {"bind_renamed_flow", "bind-empty-ws"}
    assert worker_scope == '["bind_renamed_flow"]'
    # Leave the v63 shape behind for the rest of this worker's suite.
    with write_transaction(TEST_DATABASE_URL) as conn:
        for column in (
            "default_agent_provider",
            "default_agent_model",
            "default_agent_thinking",
        ):
            conn.execute(f"alter table workspaces drop column if exists {column}")


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


def test_shared_key_fails_fast() -> None:
    """Two workspaces claiming the same key (legal under v50's free-form
    per-workspace keys) would both rename onto one id — the second parent
    insert hits workspaces_pkey mid-migration. Fail fast instead, naming
    both workspaces so the operator can point them at distinct keys."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "bind-shared-a", "duplicate_flow")
        _seed_workspace(conn, "bind-shared-b", "duplicate_flow")
        # Count rows keyed on (workspace_id, ...): both workspaces renaming
        # onto one id would collide on these composite PKs too, not just on
        # workspaces_pkey.
        for workspace_id in ("bind-shared-a", "bind-shared-b"):
            conn.execute(
                "insert into workspace_job_status_counts(workspace_id, status, cnt)"
                " values (%s, 'done', 1) on conflict do nothing",
                (workspace_id,),
            )

    from server.app.db.migrations.workspace_id_key_binding import (
        migrate_workspace_id_key_binding,
    )

    with (
        pytest.raises(RuntimeError, match="duplicate_flow <- bind-shared-a, bind-shared-b"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        migrate_workspace_id_key_binding(conn)

    # The failed transaction rolled back: neither workspace moved.
    with read_connection(TEST_DATABASE_URL) as conn:
        ids = {
            str(row["id"])
            for row in conn.execute(
                "select id from workspaces where id in ('bind-shared-a', 'bind-shared-b')"
            ).fetchall()
        }
        count_ws = {
            str(row["workspace_id"])
            for row in conn.execute(
                "select distinct workspace_id from workspace_job_status_counts"
                " where workspace_id in ('bind-shared-a', 'bind-shared-b')"
            ).fetchall()
        }
    assert ids == {"bind-shared-a", "bind-shared-b"}
    assert count_ws == {"bind-shared-a", "bind-shared-b"}


def test_illegal_legacy_key_fails_fast() -> None:
    """A legacy key that violates the v62 id contract (e.g. 'team/flow') is
    rejected up front — renaming to it would strand the workspace behind
    URL-addressed routes that can never match."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "bind-illegal-ws", "team/flow")

    from server.app.db.migrations.workspace_id_key_binding import (
        migrate_workspace_id_key_binding,
    )

    with (
        pytest.raises(RuntimeError, match="team/flow"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        migrate_workspace_id_key_binding(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select id, default_workflow_key from workspaces where id='bind-illegal-ws'"
        ).fetchone()
    assert row is not None and row["default_workflow_key"] == "team/flow"


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
    assert bad == [], "post-v62 invariant violated: every workspace has id == key"
