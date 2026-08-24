"""Schema v46: Agent definitions move from global to workspace scope.

The migration copies the globally published Agent definition of every
capability a workspace references (workflow revision nodes + materialized
node routes) into that workspace as version 1, deletes every global agent
row, and swaps the capability uniqueness index to per-workspace.
"""

from __future__ import annotations

import json

import pytest

from server.app.db.migrations import migrate_agent_workspace_scope
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_DEMO_WORKFLOW = "education_video_problems_generation"


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'demo_workflow') on conflict(id) do nothing",
        (workspace_id, workspace_id),
    )


def _seed_global_agent(
    conn, agent_id: str, capability: str, *, status: str = "published", version: int = 1
) -> None:
    definition = {"capability": capability, "runtime": "velites", "skill": "q/a"}
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    published_at = "current_timestamp" if status == "published" else "null"
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by, published_at)"
        f" values (%s, 'agent', null, %s, %s, %s, %s, %s, 'user:test', {published_at})",
        (
            f"agent:{agent_id}:v{version}",
            agent_id,
            version,
            status,
            canonical,
            f"hash-{agent_id}-v{version}",
        ),
    )


def _seed_revision(conn, workspace_id: str, workflow_key: str, capabilities: list[str]) -> None:
    definition = {
        "key": workflow_key,
        "label": workflow_key,
        "nodes": {cap: {"capability": cap} for cap in capabilities},
    }
    conn.execute(
        "insert into workflow_revisions("
        "id, workspace_id, workflow_key, version, status, definition_json, definition_hash)"
        " values (%s, %s, %s, 1, 'active', %s, 'h')",
        (
            f"{workspace_id}:{workflow_key}:v1",
            workspace_id,
            workflow_key,
            json.dumps(definition),
        ),
    )


def _agent_rows(conn, workspace_id) -> list[dict]:
    if workspace_id is None:
        where = "workspace_id is null"
        params: tuple = ()
    else:
        where = "workspace_id = %s"
        params = (workspace_id,)
    return [
        dict(row)
        for row in conn.execute(
            "select workspace_id, entity_key, version, status, definition_json, definition_hash"
            f" from versioned_entities where entity_type='agent' and {where}"
            " order by entity_key, version",
            params,
        ).fetchall()
    ]


@pytest.mark.fresh_schema
def test_migration_copies_referenced_agents_into_each_workspace() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "ws-a")
        _seed_workspace(conn, "ws-b")
        _seed_workspace(conn, "ws-unused")
        _seed_global_agent(conn, "writer", "write_script")
        _seed_global_agent(conn, "reviewer", "review_script")
        _seed_global_agent(conn, "orphan", "orphan_capability")
        _seed_global_agent(conn, "old-writer", "write_script", status="archived")
        _seed_revision(conn, "ws-a", "flow-a", ["write_script", "review_script"])
        _seed_revision(conn, "ws-b", "flow-b", ["write_script"])
        # ws-b also has a materialized route whose capability never appears in
        # its revisions: the route target's capability must be copied too.
        _seed_global_agent(conn, "router-only", "route_only_capability")
        conn.execute(
            "insert into workspace_node_routes("
            "workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values ('ws-b', 'flow-b', 'extra', 'agent', 'router-only')"
        )

        migrate_agent_workspace_scope(conn)

        ws_a = _agent_rows(conn, "ws-a")
        assert [(row["entity_key"], row["version"], row["status"]) for row in ws_a] == [
            ("reviewer", 1, "published"),
            ("writer", 1, "published"),
        ]
        ws_b = {row["entity_key"] for row in _agent_rows(conn, "ws-b")}
        assert ws_b == {"writer", "router-only"}
        assert _agent_rows(conn, "ws-unused") == []
        # Definition payload and hash survive the copy verbatim (frozen job
        # manifests keep matching the workspace rows).
        writer = next(row for row in ws_a if row["entity_key"] == "writer")
        assert json.loads(writer["definition_json"])["skill"] == "q/a"
        assert writer["definition_hash"] == "hash-writer-v1"
        # Every global agent row is gone, including archived and orphan ones.
        assert _agent_rows(conn, None) == []


@pytest.mark.fresh_schema
def test_migration_is_idempotent_on_replay() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "ws-replay")
        _seed_global_agent(conn, "writer", "write_script")
        _seed_revision(conn, "ws-replay", "flow", ["write_script"])
        migrate_agent_workspace_scope(conn)
        total = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        migrate_agent_workspace_scope(conn)
        total_after = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
    assert total_after == total


@pytest.mark.fresh_schema
def test_migration_swaps_capability_index_to_workspace_scope() -> None:
    migrate = migrate_agent_workspace_scope
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "ws-idx-a")
        _seed_workspace(conn, "ws-idx-b")
        migrate(conn)
        # Same capability published in two workspaces is legal now.
        for workspace_id in ("ws-idx-a", "ws-idx-b"):
            conn.execute(
                "insert into versioned_entities("
                "id, entity_type, workspace_id, entity_key, version, status,"
                " definition_json, definition_hash, created_by)"
                " values (%s, 'agent', %s, 'writer', 1, 'published',"
                " '{\"capability\": \"write_script\"}', 'h', 'u')",
                (f"agent:{workspace_id}:writer:v1", workspace_id),
            )
        # ...but duplicated within one workspace violates the new index.
        from psycopg import IntegrityError

        with pytest.raises(IntegrityError) as excinfo:
            conn.execute(
                "insert into versioned_entities("
                "id, entity_type, workspace_id, entity_key, version, status,"
                " definition_json, definition_hash, created_by)"
                " values ('agent:ws-idx-a:writer2:v1', 'agent', 'ws-idx-a', 'writer-2', 1,"
                " 'published', '{\"capability\": \"write_script\"}', 'h', 'u')"
            )
        assert excinfo.value.diag.constraint_name == "versioned_entities_published_capability"


def test_baseline_schema_carries_the_workspace_scoped_index() -> None:
    """The current schema (built by init_db at SCHEMA_VERSION) already has the
    per-workspace capability index — guards the postgres_schema.sql baseline
    against drifting from the migration DDL again."""
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select indexdef from pg_indexes"
            " where schemaname = current_schema()"
            " and indexname = 'versioned_entities_published_capability'"
        ).fetchone()
    assert row is not None
    definition = str(row["indexdef"])
    assert "workspace_id" in definition
    assert "capability" in definition
    assert "entity_type = 'agent'" in definition or "entity_type = 'agent'::text" in definition


@pytest.mark.fresh_schema
def test_upgrade_from_v45_with_legacy_global_index() -> None:
    """v45 → v46 upgrade path: the legacy capability-only unique index must
    not block the per-workspace copies.

    Regression test: with the pre-fix ordering (copy before the index swap),
    a real upgrade crashed with UniqueViolation because the copies share
    capabilities with the not-yet-deleted global rows. The migration tests
    above never covered this — fresh_schema builds start from the v46
    baseline, where the legacy index never exists. Here we rebuild the v45
    shape by hand (drop the new index, recreate the legacy one), seed v45
    data, then replay init_db exactly as a backend restart after deploy does.
    """
    from server.app.db.schema import SCHEMA_VERSION, init_db

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("drop index if exists versioned_entities_published_capability")
        # The v45 index: capability-only, global (init_db's baseline replay
        # skips recreating the v46 shape because the name already exists).
        conn.execute(
            "create unique index versioned_entities_published_capability"
            " on versioned_entities((definition_json::jsonb->>'capability'))"
            " where entity_type = 'agent' and status = 'published'"
        )
        _seed_workspace(conn, "ws-up-a")
        _seed_workspace(conn, "ws-up-b")
        _seed_global_agent(conn, "writer", "write_script")
        _seed_revision(conn, "ws-up-a", "flow-a", ["write_script"])
        _seed_revision(conn, "ws-up-b", "flow-b", ["write_script"])
        # Pretend v46 was never applied so init_db replays the full upgrade.
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        for workspace_id in ("ws-up-a", "ws-up-b"):
            rows = _agent_rows(conn, workspace_id)
            assert [(row["entity_key"], row["version"], row["status"]) for row in rows] == [
                ("writer", 1, "published")
            ]
        assert _agent_rows(conn, None) == []
        indexdef = str(
            conn.execute(
                "select indexdef from pg_indexes"
                " where schemaname = current_schema()"
                " and indexname = 'versioned_entities_published_capability'"
            ).fetchone()["indexdef"]
        )
        assert "workspace_id" in indexdef
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert migration["name"] == "material_bundles"
