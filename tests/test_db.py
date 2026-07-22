from freezegun import freeze_time

from server.app.db import Database
from server.app.db.schema import SCHEMA_VERSION
from tests.postgres_support import TEST_DATABASE_URL


def test_video_query_connections_use_postgres(tmp_path):
    del tmp_path
    database = Database(TEST_DATABASE_URL)

    with database._connect_read() as conn:
        row = conn.execute("select current_database() as name").fetchone()
    assert row is not None
    assert row["name"]


_EXPECTED_INDEXES = {
    "idx_videos_status",
    "idx_videos_content_type_external_id",
    "idx_videos_created_at",
    "idx_phase_runs_video_id",
    "idx_phase_runs_video_id_status",
    "idx_transcription_runs_video_id",
    "idx_jobs_status",
    "idx_executor_leases_status_expires_at",
    "idx_executor_leases_job_status",
    "idx_node_runs_run_dir",
    "idx_node_run_token_usage_job_id",
}


def test_database_creates_performance_indexes(db):
    """Regression test for issue 012: critical indexes must exist."""
    with db.connect() as conn:
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "select indexname from pg_indexes where schemaname=current_schema()"
            ).fetchall()
        }
    assert _EXPECTED_INDEXES.issubset(indexes), f"Missing indexes: {_EXPECTED_INDEXES - indexes}"


@freeze_time("2024-01-01T00:00:00", auto_tick_seconds=0.01)
def test_insert_and_list_packages(db):
    db.insert_package("/tmp/packages/test-a.zip")
    db.insert_package("/tmp/packages/test-b.zip")

    packages = db.list_packages(limit=10)
    assert len(packages) == 2
    # Most recent first
    assert packages[0]["path"] == "/tmp/packages/test-b.zip"
    assert packages[1]["path"] == "/tmp/packages/test-a.zip"

    limited = db.list_packages(limit=1)
    assert len(limited) == 1
    assert limited[0]["path"] == "/tmp/packages/test-b.zip"


def test_insert_package_with_metadata(db):
    db.insert_package("/tmp/p.zip", name="批次 A", video_count=10, size_bytes=1024)
    packages = db.list_packages(limit=10)
    assert len(packages) == 1
    assert packages[0]["name"] == "批次 A"
    assert packages[0]["video_count"] == 10
    assert packages[0]["size_bytes"] == 1024


def test_delete_package(db):
    db.insert_package("/tmp/p.zip", name="批次 A", video_count=10, size_bytes=1024)
    pkg = db.list_packages(limit=1)[0]
    db.delete_package(pkg["id"])
    assert db.list_packages(limit=10) == []


def test_update_package_name(db):
    db.insert_package("/tmp/p.zip", name="旧名称", video_count=1, size_bytes=100)
    pkg = db.list_packages(limit=1)[0]
    db.update_package_name(pkg["id"], "新名称")
    assert db.list_packages(limit=1)[0]["name"] == "新名称"


def test_database_initialization_records_postgres_schema(db):
    """Database construction records the PostgreSQL control-plane schema."""
    with db.connect() as conn:
        versions = {
            row["version"]
            for row in conn.execute("select version from schema_migrations").fetchall()
        }
    assert versions == {SCHEMA_VERSION}
