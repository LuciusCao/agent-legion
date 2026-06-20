from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from tests.executors.legacy.helpers import (
    _insert_legacy_agent_assignment,
    _seed_default_workspace_assignment,
)
from tests.helpers import ensure_legacy_workspace_tables


def test_app_startup_materializes_executor_configuration_without_worker(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        response = client.get("/api/workspaces/default/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi-default",
    }
    backups = list(tmp_path.glob("video_hive-before-v005-*.sqlite"))
    assert len(backups) == 1


def test_app_startup_materialization_is_idempotent(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        first = client.get("/api/workspaces/default/executor-configuration").json()

    app2 = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app2) as client:
        second = client.get("/api/workspaces/default/executor-configuration").json()

    assert first == second


def test_app_startup_preserves_user_modified_executor_configuration(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        client.get("/api/workspaces/default/executor-configuration")

    with app.state.job_db.connect() as conn:
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit = ? "
            "where workspace_id = ? and executor_id = ?",
            (999, "default", "local-default"),
        )

    app2 = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app2) as client:
        config = client.get("/api/workspaces/default/executor-configuration").json()

    allocations = {row["executor_id"]: row["concurrency_limit"] for row in config["allocations"]}
    assert allocations["local-default"] == 999
    assert allocations["pi-default"] == 3


def test_app_startup_aborts_when_finalization_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    queries.create_workspace("default", default_workflow_key="question_comprehension_info")
    _insert_legacy_agent_assignment(queries, "default", "unknown-agent", 2)

    with pytest.raises(RuntimeError, match="finalize-workspace-executor-migration.py --check"):
        create_app(data_dir=tmp_path, start_worker=False)
