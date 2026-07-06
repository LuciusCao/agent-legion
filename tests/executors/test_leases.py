from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.jobs import JobQueries
from tests.helpers.executor_worker import allocate, bind


def _write_events(run_dir: Path, events: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


@pytest.fixture
def lease_repo(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    jobs_dir = data_dir / "jobs"
    db_path = tmp_path / "jobs.sqlite"
    job_db = JobQueries(db_path, jobs_dir)
    repo = ExecutorLeaseRepository(db_path, job_db=job_db, data_dir=data_dir)
    return repo, job_db, data_dir


def _setup_workspace_and_job(job_db: JobQueries) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, ?)",
            ("ws-1", "Test"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (?, ?, ?, ?, ?)",
            ("job-1", "ws-1", "question_comprehension_info", "question", "q-1"),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values (?, ?, ?)",
            ("job-1", "review_keywords", "pending"),
        )


def test_finish_lease_captures_token_usage(lease_repo):
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)

    workspace_id = "ws-1"
    workflow_key = "question_comprehension_info"
    node_key = "review_keywords"
    executor_id = "pi-1"

    allocate(job_db, workspace_id, executor_id, 10)
    bind(job_db, workspace_id, workflow_key, node_key, executor_id)

    claimed = repo.try_claim(
        LeaseClaimRequest(
            executor_id=executor_id,
            global_capacity=10,
            workspace_id=workspace_id,
            job_id="job-1",
            workflow_key=workflow_key,
            node_key=node_key,
            capability="review_keywords",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path=str(data_dir / "logs" / "run.log"),
        )
    )
    assert claimed is not None

    run_token = "run-token"
    run_dir = data_dir / "jobs" / workspace_id / "job-1" / "runs" / node_key / run_token
    _write_events(
        run_dir,
        [
            {
                "type": "message_end",
                "message": {"usage": {"input": 20, "output": 10, "cacheRead": 2}},
            }
        ],
    )
    (run_dir / "run.json").write_text(
        json.dumps({"model": {"provider": "gateway", "model": "your-model-a"}, "skill_version": "v2"}),
        encoding="utf-8",
    )

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi",),
        log_path="",
        run_dir=str(run_dir.relative_to(data_dir)),
    )

    assert repo.finish(claimed.lease_id, result) is True

    with connect_sqlite(repo.path) as conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is not None
    assert row["input_tokens"] == 20
    assert row["output_tokens"] == 10
    assert row["cache_read_tokens"] == 2
    assert row["total_tokens"] == 32
    assert row["message_count"] == 1
    assert row["workspace_id"] == workspace_id
    assert row["skill_version"] == "v2"


def test_finish_lease_missing_events_does_not_fail_lease(lease_repo):
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)

    workspace_id = "ws-1"
    workflow_key = "question_comprehension_info"
    node_key = "review_keywords"
    executor_id = "pi-1"

    allocate(job_db, workspace_id, executor_id, 10)
    bind(job_db, workspace_id, workflow_key, node_key, executor_id)

    claimed = repo.try_claim(
        LeaseClaimRequest(
            executor_id=executor_id,
            global_capacity=10,
            workspace_id=workspace_id,
            job_id="job-1",
            workflow_key=workflow_key,
            node_key=node_key,
            capability="review_keywords",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path=str(data_dir / "logs" / "run.log"),
        )
    )
    assert claimed is not None

    # Run directory exists but has no events.jsonl.
    run_dir = data_dir / "jobs" / workspace_id / "job-1" / "runs" / node_key / "empty"
    run_dir.mkdir(parents=True)

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi",),
        log_path="",
        run_dir=str(run_dir.relative_to(data_dir)),
    )

    assert repo.finish(claimed.lease_id, result) is True

    with connect_sqlite(repo.path) as conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is None
