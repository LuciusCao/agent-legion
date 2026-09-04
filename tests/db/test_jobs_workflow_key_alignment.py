"""Schema v68 migration tests: workflow_key alignment (#211 Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.migrations.jobs_workflow_key_alignment import (
    migrate_jobs_workflow_key_alignment,
)
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.helpers.legacy_workflow_key_shape import (
    narrow_back_to_v70,
    restore_pre_v70_shape,
)
from tests.postgres_support import TEST_DATABASE_URL


# #211 M2 (schema v70): the workflow_key columns are gone from the live
# tables, so the "pre-v62 shape" fixtures below (key != id rows used to
# exercise the v68 rewrite passes) cannot be seeded on a fresh database
# directly. The row-surgery migrations still run with full effect on real
# pre-v68→v70 upgrades (old installs keep their columns through the schema
# replay), so these tests rebuild the pre-v70 shape first — add the columns
# back with their old PKs/uniques — seed the stale rows, run the migration,
# and assert the rewrite semantics exactly as before (subagent review #334:
# skipping them left the revision version-shift / state-table twin /
# counts-merge SQL with zero coverage while it still ships).
@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    init_db(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def legacy_shape_db(fresh_db) -> str:
    """fresh_db with the v69 (pre-v70) column/PK shape restored, so the v68
    rewrite passes run against the shape real upgrades present them; torn
    back down to the terminal shape afterwards so later tests on the shared
    database see the v70 catalog."""
    with write_transaction(fresh_db) as conn:
        restore_pre_v70_shape(conn)
    yield fresh_db
    with write_transaction(fresh_db) as conn:
        narrow_back_to_v70(conn)


def test_migration_aligns_stale_keys(legacy_shape_db) -> None:
    """Pre-v62 rows (workspace_id renamed, workflow_key left behind) are
    rewritten to the workspace id across every live table (Codex P1 on
    #313/#315)."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-aligned', 'WS', 'ws-aligned')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir)"
            " values ('j-stale', 'ws-aligned', 'old_key', 'test', 's1', 't', 'pending', 'd'),"
            " ('j-fresh', 'ws-aligned', 'ws-aligned', 'test', 's2', 't', 'pending', 'd')"
        )
        conn.execute(
            "insert into runs(id, workspace_id, workflow_key, source_kind)"
            " values ('r-stale', 'ws-aligned', 'old_key', 'test')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, "
            " target_kind, target_id) values ('ws-aligned', 'n1', 'agent', 'a1')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into workspace_node_limits(workspace_id, node_key, "
            " concurrency_limit) values ('ws-aligned', 'n1', 2)"
            " on conflict do nothing"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        jobs = {
            str(row["id"]): str(row["workflow_key"])
            for row in conn.execute("select id, workflow_key from jobs").fetchall()
        }
        run_key = str(
            conn.execute("select workflow_key from runs where id='r-stale'").fetchone()[
                "workflow_key"
            ]
        )
        route_key = str(
            conn.execute(
                "select workflow_key from workspace_node_routes where node_key='n1'"
            ).fetchone()["workflow_key"]
        )
        limit_key = str(
            conn.execute(
                "select workflow_key from workspace_node_limits where node_key='n1'"
            ).fetchone()["workflow_key"]
        )
    assert jobs == {"j-stale": "ws-aligned", "j-fresh": "ws-aligned"}
    assert run_key == "ws-aligned"
    assert route_key == "ws-aligned"
    assert limit_key == "ws-aligned"


def test_migration_aligns_node_code_entity_keys(fresh_db) -> None:
    """node_code entity keys ("<key>:<node>") are re-prefixed with the
    workspace id — NodeCodeService lookups pass the identity value and must
    find the stored rows (Codex P1 on #315)."""
    with write_transaction(fresh_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-entity', 'WS', 'ws-entity')"
        )
        for entity_id, entity_key, version in (
            ("e1", "old_key:fetch", 1),
            ("e2", "old_key:parse", 1),
            ("e3", "ws-entity:fetch", 2),
        ):
            conn.execute(
                "insert into versioned_entities(id, entity_type, workspace_id,"
                " entity_key, version, status, definition_json, definition_hash,"
                " created_by) values (%s, 'node_code', 'ws-entity', %s, %s,"
                " 'published', '{}', 'h', 'system') on conflict do nothing",
                (entity_id, entity_key, version),
            )
        # Agent-definition rows key on agent_id (no workflow segment): untouched.
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('e-agent', 'agent', 'ws-entity',"
            " 'agent_x', 1, 'published', '{}', 'h', 'system') on conflict do nothing"
        )

    with write_transaction(fresh_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(fresh_db) as conn:
        rows = {
            str(row["entity_key"])
            for row in conn.execute(
                "select entity_key from versioned_entities where workspace_id='ws-entity'"
            ).fetchall()
        }
    # old_key:fetch (published v1) collides with the newer ws-entity:fetch (v2)
    # on the published uniqueness — the window-era row wins, the old-key row
    # drops. old_key:parse re-prefixes cleanly.
    assert rows == {"ws-entity:fetch", "ws-entity:parse", "agent_x"}


def test_migration_moves_node_status_counts(legacy_shape_db) -> None:
    """Counts follow the jobs rewrite: the rekey trigger moves live counts and
    the merge pass folds any remaining old-key rows into workspace-id rows
    (P3-1 on PR #313)."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-counts', 'WS', 'ws-counts')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir)"
            " values ('j-counts', 'ws-counts', 'old_key', 'test', 's1', 't', 'pending', 'd')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status)"
            " values ('j-counts', 'fetch', 'pending')"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        rows = {
            (str(row["workflow_key"]), str(row["node_key"])): int(row["cnt"])
            for row in conn.execute(
                "select workflow_key, node_key, cnt from workspace_job_node_status_counts"
                " where workspace_id='ws-counts'"
            ).fetchall()
        }
    assert rows == {("ws-counts", "fetch"): 1}


def test_migration_is_idempotent(legacy_shape_db) -> None:
    """A second run finds no divergent rows and is a no-op."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-idem', 'WS', 'ws-idem')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir)"
            " values ('j-idem', 'ws-idem', 'old', 'test', 's1', 't', 'pending', 'd')"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        row = conn.execute("select workflow_key from jobs where id='j-idem'").fetchone()
    assert str(row["workflow_key"]) == "ws-idem"


def test_upgrade_path_applies_alignment(tmp_path: Path) -> None:
    """Upgraded databases record every alignment-era version (v68 data
    migration, v69 lease index); the pin anchors the registry tail."""
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=68").fetchone()
        tail = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert str(row["name"]) == "jobs_workflow_key_alignment"
    assert tail is not None
    # The registry tail at the CURRENT schema version (#434 renumber: v76 is
    # studio_publish_requests).
    assert str(tail["name"]) == "studio_publish_requests"


def test_aligned_entity_history_is_preserved(fresh_db) -> None:
    """Codex P1 on #315: already-aligned multi-version rows must NOT pair
    with each other in the collision pre-pass — the unaligned guard keeps
    the whole version history (ordinary dispatch finds the published code,
    pinned replays keep their frozen versions)."""
    with write_transaction(fresh_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-hist', 'WS', 'ws-hist')"
        )
        for entity_id, version, status in (
            ("h1", 1, "archived"),
            ("h2", 2, "archived"),
            ("h3", 3, "published"),
        ):
            conn.execute(
                "insert into versioned_entities(id, entity_type, workspace_id,"
                " entity_key, version, status, definition_json, definition_hash,"
                " created_by) values (%s, 'node_code', 'ws-hist', 'ws-hist:fetch',"
                " %s, %s, '{}', 'h', 'system') on conflict do nothing",
                (entity_id, version, status),
            )

    with write_transaction(fresh_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(fresh_db) as conn:
        rows = [
            (str(row["entity_key"]), int(row["version"]))
            for row in conn.execute(
                "select entity_key, version from versioned_entities"
                " where workspace_id='ws-hist' order by version"
            ).fetchall()
        ]
    assert rows == [("ws-hist:fetch", 1), ("ws-hist:fetch", 2), ("ws-hist:fetch", 3)]


def test_state_table_twins_drop_old_key_row(legacy_shape_db) -> None:
    """Window-era routes/limits rows under the workspace id win; the old-key
    twin drops instead of colliding on the composite PK (Codex P1 on #315)."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-twins', 'WS', 'ws-twins')"
        )
        for key in ("old_key", "ws-twins"):
            conn.execute(
                "insert into workspace_node_routes(workspace_id, workflow_key,"
                " node_key, target_kind, target_id)"
                " values ('ws-twins', %s, 'n1', 'agent', 'a1') on conflict do nothing",
                (key,),
            )
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key,"
            " node_key, target_kind, target_id)"
            " values ('ws-twins', 'old_key', 'n2', 'agent', 'a2') on conflict do nothing"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        rows = {
            str(row["node_key"]): str(row["workflow_key"])
            for row in conn.execute(
                "select node_key, workflow_key from workspace_node_routes"
                " where workspace_id='ws-twins'"
            ).fetchall()
        }
    assert rows == {"n1": "ws-twins", "n2": "ws-twins"}


def test_revisions_shift_past_window_era_versions(legacy_shape_db) -> None:
    """Window-era publishes numbered from 1 (the counter could not see the
    old-key rows); the old history shifts above the window-era maximum
    instead of colliding on unique(workspace_id, workflow_key, version)."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-rev', 'WS', 'ws-rev')"
        )
        # Old-key history: v1, v2. Window era republished: v1 (numbering from 1).
        for version in (1, 2):
            conn.execute(
                "insert into workflow_revisions(id, workspace_id, workflow_key,"
                " version, status, definition_json, definition_hash)"
                " values (%s, 'ws-rev', 'old_key', %s, 'archived', '{}', 'h')"
                " on conflict do nothing",
                (f"rev-old-{version}", version),
            )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, workflow_key,"
            " version, status, definition_json, definition_hash)"
            " values ('rev-new-1', 'ws-rev', 'ws-rev', 1, 'active', '{}', 'h')"
            " on conflict do nothing"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        rows = sorted(
            (str(row["workflow_key"]), int(row["version"]), str(row["status"]))
            for row in conn.execute(
                "select workflow_key, version, status from workflow_revisions"
                " where workspace_id='ws-rev'"
            ).fetchall()
        )
    # The uniform offset is the workspace's overall version max (2): old
    # v1/v2 -> 3/4, above the window-era v1 which stays active.
    assert rows == [
        ("ws-rev", 1, "active"),
        ("ws-rev", 3, "archived"),
        ("ws-rev", 4, "archived"),
    ]


def test_old_key_active_revision_archived(legacy_shape_db) -> None:
    """Codex P1 on #315: a workspace republished during the v62→v68 window
    carries one active revision per key. The identity-key active stays
    authoritative; the shifted old-key active must be archived, or the
    elevated old DAG would outrank the window-era publish."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-dual', 'WS', 'ws-dual')"
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, workflow_key,"
            " version, status, definition_json, definition_hash)"
            " values ('rev-old-a', 'ws-dual', 'old_key', 1, 'active', '{}', 'h')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, workflow_key,"
            " version, status, definition_json, definition_hash)"
            " values ('rev-new-a', 'ws-dual', 'ws-dual', 1, 'active', '{}', 'h')"
            " on conflict do nothing"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        rows = sorted(
            (int(row["version"]), str(row["status"]))
            for row in conn.execute(
                "select version, status from workflow_revisions where workspace_id='ws-dual'"
            ).fetchall()
        )
        active = conn.execute(
            "select count(*) as n from workflow_revisions"
            " where workspace_id='ws-dual' and status='active'"
        ).fetchone()
    assert rows == [(1, "active"), (2, "archived")]
    assert int(active["n"]) == 1


def test_entity_history_survives_draft_only_collision(fresh_db) -> None:
    """Codex P1 on #315: an old-key published version must survive a window
    era that only drafted — dispatch keeps resolvable published code and the
    draft twin (same version, non-published) drops."""
    with write_transaction(fresh_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-draft', 'WS', 'ws-draft')"
        )
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('d-old', 'node_code', 'ws-draft',"
            " 'old_key:fetch', 1, 'published', '{}', 'h', 'system')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('d-new', 'node_code', 'ws-draft',"
            " 'ws-draft:fetch', 1, 'draft', '{}', 'h', 'user:u')"
            " on conflict do nothing"
        )

    with write_transaction(fresh_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(fresh_db) as conn:
        rows = sorted(
            (str(row["entity_key"]), int(row["version"]), str(row["status"]))
            for row in conn.execute(
                "select entity_key, version, status from versioned_entities"
                " where workspace_id='ws-draft'"
            ).fetchall()
        )
    # Same-version twins cannot coexist (version is a uniqueness member):
    # the old-key published row wins over the window-era draft — dispatch
    # keeps resolvable code.
    assert rows == [("ws-draft:fetch", 1, "published")]


def test_sole_old_key_active_survives(legacy_shape_db) -> None:
    """Codex P1 on #315: a workspace never republished during the window has
    its ONLY active revision under the old key — it must stay active through
    the rewrite (intake/Studio/worker all resolve the active definition)."""
    with write_transaction(legacy_shape_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-sole', 'WS', 'ws-sole')"
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, workflow_key,"
            " version, status, definition_json, definition_hash)"
            " values ('rev-sole', 'ws-sole', 'old_key', 1, 'active', '{}', 'h')"
            " on conflict do nothing"
        )

    with write_transaction(legacy_shape_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(legacy_shape_db) as conn:
        rows = [
            (str(row["workflow_key"]), str(row["status"]))
            for row in conn.execute(
                "select workflow_key, status from workflow_revisions where workspace_id='ws-sole'"
            ).fetchall()
        ]
    assert rows == [("ws-sole", "active")]


def test_frozen_pin_hash_survives_twin_demotion_order(fresh_db) -> None:
    """Codex P1 on #315: old-key published v1 with a window era that
    published v1 (now archived) then v2 (published). The twin pass runs
    BEFORE the demotion, so the old v1 keeps its exact definition_hash —
    a replay frozen on {version: 1, hash: old} still resolves."""
    with write_transaction(fresh_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-pin', 'WS', 'ws-pin')"
        )
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('p-old', 'node_code', 'ws-pin',"
            " 'old_key:fetch', 1, 'published', '{}', 'old-hash', 'system')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('p-w1', 'node_code', 'ws-pin',"
            " 'ws-pin:fetch', 1, 'archived', '{}', 'w1-hash', 'user:u')"
            " on conflict do nothing"
        )
        conn.execute(
            "insert into versioned_entities(id, entity_type, workspace_id,"
            " entity_key, version, status, definition_json, definition_hash,"
            " created_by) values ('p-w2', 'node_code', 'ws-pin',"
            " 'ws-pin:fetch', 2, 'published', '{}', 'w2-hash', 'user:u')"
            " on conflict do nothing"
        )

    with write_transaction(fresh_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(fresh_db) as conn:
        rows = sorted(
            (int(row["version"]), str(row["status"]), str(row["definition_hash"]))
            for row in conn.execute(
                "select version, status, definition_hash from versioned_entities"
                " where workspace_id='ws-pin'"
            ).fetchall()
        )
    # The old v1 (old-hash) survives — the twin pass ran BEFORE the
    # demotion, so the exact hash a frozen pin references stays resolvable.
    # The window-era v1 twin (w1-hash) loses the same-version published
    # contest — one row per version is the table's uniqueness contract.
    assert rows == [
        (1, "archived", "old-hash"),
        (2, "published", "w2-hash"),
    ]


def test_both_published_same_version_twin_resolves(fresh_db) -> None:
    """Subagent P1 on #315: old-key published v1 + window-era published v1
    under the identity key. The plain rewrite aborted the whole upgrade on
    the entity uniqueness; the both-published twin rule now drops the
    old-key row — version numbers identify the CURRENT publish history of
    the entity-key domain, which the window era restarted."""
    with write_transaction(fresh_db) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-both', 'WS', 'ws-both')"
        )
        for entity_id, key, hash_ in (
            ("b-old", "old_key:fetch", "old-hash"),
            ("b-new", "ws-both:fetch", "new-hash"),
        ):
            conn.execute(
                "insert into versioned_entities(id, entity_type, workspace_id,"
                " entity_key, version, status, definition_json, definition_hash,"
                " created_by) values (%s, 'node_code', 'ws-both', %s, 1,"
                " 'published', '{}', %s, 'x') on conflict do nothing",
                (entity_id, key, hash_),
            )

    with write_transaction(fresh_db) as conn:
        migrate_jobs_workflow_key_alignment(conn)

    with read_connection(fresh_db) as conn:
        rows = [
            (str(row["entity_key"]), int(row["version"]), str(row["status"]))
            for row in conn.execute(
                "select entity_key, version, status from versioned_entities"
                " where workspace_id='ws-both'"
            ).fetchall()
        ]
    assert rows == [("ws-both:fetch", 1, "published")]
