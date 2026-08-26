"""Shared Postgres schema assertions.

The unit tier (``tests/db/test_postgres_runtime.py``) and the full-gate
evidence (``tests/full/test_agent_worker_control_plane.py``) assert the same
schema-idempotency contract; the body lives here so the full-gate file does
not import a unit test module.
"""

from __future__ import annotations

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def assert_schema_initialization_is_idempotent() -> None:
    init_db(TEST_DATABASE_URL)
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema=current_schema()"
        ).fetchall()
    names = {str(row["table_name"]) for row in rows}
    assert {
        "jobs",
        "executor_leases",
        "node_shards",
        "versioned_entities",
        "agent_workers",
        "agent_execution_requests",
        "workspace_node_routes",
        "workspace_node_capacities",
        "workspace_agent_capacities",
        "users",
        "sessions",
        "workspace_members",
    } <= names
    # schema v27 cutover dropped the YAML-synced catalog table.
    assert "agent_definitions" not in names
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='agent_workers'"
            ).fetchall()
        }
    assert {"capabilities_json", "models_json"} <= columns
