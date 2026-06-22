import gc
import sqlite3
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import Migration, run_migrations
from server.app.db.schema import init_db
from server.app.executors.backup import backup_sqlite_connection
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.jobs.queries import JobQueries


def _assert_no_resource_warning() -> None:
    """Force a full GC cycle and fail on any SQLite ResourceWarning."""
    gc.collect()


def test_init_db_closes_connection(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        init_db(path)
        _assert_no_resource_warning()


def test_query_helpers_close_connections(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    init_db(path)

    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        queries = JobQueries(path, jobs_dir)
        queries.create_workspace("lifecycle")
        _assert_no_resource_warning()


def test_executor_lease_repository_closes_connections(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    init_db(path)

    queries = JobQueries(path, jobs_dir)
    queries.create_workspace("lifecycle", default_workflow_key="question_comprehension_info")
    queries.update_workspace_configuration(
        "lifecycle",
        name="lifecycle",
        description="",
        default_workflow_key="question_comprehension_info",
        default_entity="question",
        resource_config={},
        intake_config={},
        executor_allocations=[{"executor_id": "local", "concurrency_limit": 1}],
        node_bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "extract_keywords",
                "executor_id": "local",
            }
        ],
        node_limits=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "extract_keywords",
                "concurrency_limit": 1,
            }
        ],
    )
    queries.create_job(
        "question_comprehension_info",
        "question_id",
        "q1",
        "",
        "Job",
        ["extract_keywords"],
        workspace_id="lifecycle",
    )

    repo = ExecutorLeaseRepository(path)
    request = LeaseClaimRequest(
        executor_id="local",
        global_capacity=1,
        workspace_id="lifecycle",
        job_id="lifecycle_question_comprehension_info_q1",
        workflow_key="question_comprehension_info",
        node_key="extract_keywords",
        capability="extract_keywords",
        local_node_limit=1,
        lease_ttl_seconds=60,
        log_path="",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        result = repo.try_claim(request)
        assert result is not None, "lease claim should succeed with valid setup"
        repo.heartbeat(result.lease_id, 60)
        repo.finish(
            result.lease_id,
            ExecutionResult(status="completed", exit_code=0),
        )
        repo.expire_stale(datetime.now(UTC) + timedelta(seconds=120))
        _assert_no_resource_warning()


def test_backup_closes_destination_connection(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    init_db(path)
    backup_path = tmp_path / "backup.sqlite"

    source = connect_sqlite(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            backup_sqlite_connection(source, backup_path)
            _assert_no_resource_warning()
    finally:
        source.close()


def test_run_migrations_does_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "migrations.sqlite"

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("create table lifecycle_test (id integer primary key)")

    migrations = (Migration(version=1, name="lifecycle", apply=apply),)

    source = connect_sqlite(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            run_migrations(source, migrations)
            _assert_no_resource_warning()
    finally:
        source.close()


def test_bare_connect_sqlite_without_close_warns(tmp_path: Path) -> None:
    """Sanity check: an unclosed connection really does emit ResourceWarning."""
    path = tmp_path / "leak.sqlite"
    init_db(path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        connect_sqlite(path)  # intentionally dropped
        gc.collect()

    assert any(
        issubclass(w.category, ResourceWarning) and "database" in str(w.message).lower()
        for w in caught
    )
