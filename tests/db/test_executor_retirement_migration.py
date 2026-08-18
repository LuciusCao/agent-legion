"""Schema v47: executor concept retirement — harvest, drop, entity cleanup."""

from __future__ import annotations

import hashlib
import json
import logging

import pytest
from psycopg import IntegrityError

from server.app.db.migrations import migrate_executor_retirement
from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "connection": {"type": "string", "default": "cms-internal"},
        # Reserved key colliding with the platform merge (§9 transcribe_video
        # shape): must be stripped from the injected schema and become a
        # node config value instead.
        "timeout_seconds": {"type": "integer", "default": 900, "minimum": 1},
    },
}


def _pre_v47_surface(conn) -> None:
    """Recreate the pre-v47 tables/constraint the migration expects to find."""
    conn.execute(
        "alter table versioned_entities"
        " drop constraint if exists versioned_entities_entity_type_check"
    )
    conn.execute(
        "alter table versioned_entities"
        " add constraint versioned_entities_entity_type_check"
        " check(entity_type in ('node_code', 'agent', 'executor'))"
    )
    conn.execute(
        "create table if not exists workspace_executor_allocations ("
        " workspace_id text not null, executor_id text not null,"
        " concurrency_limit integer not null, primary key(workspace_id, executor_id))"
    )
    conn.execute(
        "create table if not exists workspace_node_bindings ("
        " workspace_id text not null, workflow_key text not null, node_key text not null,"
        " executor_id text not null, primary key(workspace_id, workflow_key, node_key))"
    )


def _seed_executor(
    conn, capabilities: dict, *, kind: str = "code", capacity: int = 16, key: str = "code-default"
) -> None:
    definition = {
        "kind": kind,
        "global_capacity": capacity,
        "capabilities": capabilities,
    }
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by)"
        " values (%s, %s, null, %s, 4, 'published', %s, 'hash', 'system')",
        (f"executor:{key}:v4", "executor", key, json.dumps(definition)),
    )


def _seed_revision(conn, workspace_id: str, nodes: dict) -> str:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'wf') on conflict do nothing",
        (workspace_id, workspace_id),
    )
    payload = {
        "key": "wf",
        "label": "Wf",
        "schema_version": 2,
        "intake": {"modes": {}},
        "nodes": nodes,
        "edges": [],
    }
    definition_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision_id = f"rev-{workspace_id}"
    conn.execute(
        "insert into workflow_revisions("
        "id, workspace_id, workflow_key, version, status, definition_json, definition_hash)"
        " values (%s, %s, 'wf', 1, 'active', %s, %s)",
        (
            revision_id,
            workspace_id,
            definition_json,
            hashlib.sha256(definition_json.encode("utf-8")).hexdigest(),
        ),
    )
    return revision_id


def _revision_payload(conn, revision_id: str) -> dict:
    row = conn.execute(
        "select definition_json, definition_hash from workflow_revisions where id=%s",
        (revision_id,),
    ).fetchone()
    return json.loads(row["definition_json"])


def test_schema_v49_recorded() -> None:
    assert SCHEMA_VERSION == 49
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "node_runs_config_snapshot"


@pytest.mark.fresh_schema
def test_tables_dropped_and_executor_entities_deleted() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"fetch_questions": {"config_schema": _FETCH_SCHEMA}})
        conn.execute(
            "insert into workspace_executor_allocations values ('ws-a', 'code-default', 16)"
        )
        conn.execute(
            "insert into workspace_node_bindings values ('ws-a', 'wf', 'fetch', 'code-default')"
        )
        migrate_executor_retirement(conn)
        tables = conn.execute(
            "select to_regclass('public.workspace_executor_allocations') as a,"
            " to_regclass('public.workspace_node_bindings') as b"
        ).fetchone()
        remaining = conn.execute(
            "select count(*) as c from versioned_entities where entity_type='executor'"
        ).fetchone()
    assert tables["a"] is None and tables["b"] is None
    assert remaining["c"] == 0


@pytest.mark.fresh_schema
def test_entity_type_check_rejects_executor_after_migration() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('executor:x:v1', 'executor', null, 'x', 1, 'published',"
            " '{}', 'hash', 'system')"
        )
    # node_code/agent rows still insert fine.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('node_code:wf/n:v1', 'node_code', null, 'wf/n', 1, 'published',"
            " '{}', 'hash', 'system')"
        )


@pytest.mark.fresh_schema
def test_harvest_injects_schema_and_reserved_config() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(
            conn,
            {
                "fetch_questions": {
                    "config_schema": _FETCH_SCHEMA,
                    "timeout_seconds": 700,
                    "sandbox_network": True,
                }
            },
        )
        revision_id = _seed_revision(
            conn,
            "ws-harvest",
            {
                "fetch": {"capability": "fetch_questions", "config": {"country_id": "1"}},
                "review": {"capability": "review_keywords"},
            },
        )
        migrate_executor_retirement(conn)
        payload = _revision_payload(conn, revision_id)

    node = payload["nodes"]["fetch"]
    # The reserved key is stripped from the injected schema and becomes a
    # config value (schema-declared default 900 wins over the executor-level
    # 700); the executor-level network opt-in lands in config too; existing
    # config keys are preserved.
    assert node["config_schema"] == {
        "type": "object",
        "properties": {"connection": {"type": "string", "default": "cms-internal"}},
    }
    assert node["config"] == {
        "country_id": "1",
        "timeout_seconds": 900,
        "sandbox_network": True,
    }
    # Agent-less capability without a harvest entry stays untouched.
    assert "config_schema" not in payload["nodes"]["review"]
    # The stored hash matches the rewritten payload.
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_json, definition_hash from workflow_revisions where id=%s",
            (revision_id,),
        ).fetchone()
    assert row["definition_hash"] == hashlib.sha256(row["definition_json"].encode()).hexdigest()


@pytest.mark.fresh_schema
def test_harvest_skips_agent_routed_nodes() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"review_keywords": {"config_schema": _FETCH_SCHEMA}})
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-agent', 'ws-agent', 'wf')"
        )
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:ws-agent:a1:v1', 'agent', 'ws-agent', 'a1', 1, 'published',"
            " %s, 'hash', 'system')",
            (json.dumps({"capability": "review_keywords", "runtime": "pi"}),),
        )
        revision_id = _seed_revision(
            conn, "ws-agent", {"review": {"capability": "review_keywords"}}
        )
        migrate_executor_retirement(conn)
        payload = _revision_payload(conn, revision_id)
    assert payload["nodes"]["review"] == {"capability": "review_keywords"}


@pytest.mark.fresh_schema
def test_harvest_is_idempotent_and_preserves_existing_config() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"fetch_questions": {"config_schema": _FETCH_SCHEMA}})
        revision_id = _seed_revision(
            conn,
            "ws-idem",
            {"fetch": {"capability": "fetch_questions", "config": {"timeout_seconds": 30}}},
        )
        migrate_executor_retirement(conn)
        first = _revision_payload(conn, revision_id)
        # The executor rows are gone after the first run: the replay harvests
        # nothing and rewrites nothing.
        migrate_executor_retirement(conn)
        second = _revision_payload(conn, revision_id)
    # The node's own config value wins over the harvested default.
    assert first["nodes"]["fetch"]["config"]["timeout_seconds"] == 30
    assert first == second


@pytest.mark.fresh_schema
def test_code_capacity_written_only_when_non_default() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"fetch_questions": {}}, capacity=32)
        migrate_executor_retirement(conn)
        row = conn.execute("select value from global_settings where key='instance'").fetchone()
    assert row is not None
    assert json.loads(row["value"])["code_capacity"] == 32


@pytest.mark.fresh_schema
def test_code_capacity_default_is_not_written() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"fetch_questions": {}}, capacity=16)
        migrate_executor_retirement(conn)
        row = conn.execute("select value from global_settings where key='instance'").fetchone()
    assert row is None or "code_capacity" not in json.loads(row["value"])


@pytest.mark.fresh_schema
def test_orphan_capability_and_noncode_kind_warn(caplog) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _pre_v47_surface(conn)
        _seed_executor(conn, {"unused_capability": {"config_schema": _FETCH_SCHEMA}})
        _seed_executor(conn, {}, kind="pi", key="pi-default")
        with caplog.at_level(
            logging.WARNING, logger="server.app.db.migrations.executor_retirement"
        ):
            migrate_executor_retirement(conn)
    messages = [r.getMessage() for r in caplog.records]
    assert any("unused_capability" in m and "not referenced" in m for m in messages)
    assert any("non-code executor" in m for m in messages)
