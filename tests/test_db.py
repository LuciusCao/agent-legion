from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_video_query_connections_use_postgres(tmp_path):
    del tmp_path
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select current_database() as name").fetchone()
    assert row is not None
    assert row["name"]


_EXPECTED_INDEXES = {
    "idx_jobs_status",
    "idx_executor_leases_status_expires_at",
    "idx_executor_leases_job_status",
    "idx_node_runs_run_dir",
    "idx_node_run_token_usage_job_id",
}


def test_database_creates_performance_indexes():
    """Regression test for issue 012: critical indexes must exist."""
    with read_connection(TEST_DATABASE_URL) as conn:
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "select indexname from pg_indexes where schemaname=current_schema()"
            ).fetchall()
        }
    assert _EXPECTED_INDEXES.issubset(indexes), f"Missing indexes: {_EXPECTED_INDEXES - indexes}"


def test_database_initialization_records_postgres_schema():
    """Database initialization records the PostgreSQL control-plane schema."""
    with read_connection(TEST_DATABASE_URL) as conn:
        versions = {
            row["version"]
            for row in conn.execute("select version from schema_migrations").fetchall()
        }
    assert versions == {SCHEMA_VERSION}


def test_workspaces_table_has_node_config_column():
    """Schema v14: workspaces carry per-node config overrides (spec D8)."""
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_name='workspaces' and table_schema=current_schema()"
            ).fetchall()
        }
    assert "node_config_json" in columns
