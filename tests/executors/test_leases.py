from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.jobs import JobQueries
from server.app.services import token_usage_lease
from tests.helpers.executor_worker import allocate, bind
from tests.postgres_support import TEST_DATABASE_URL


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
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir)
    repo = ExecutorLeaseRepository(db_path, job_db=job_db, data_dir=data_dir)
    return repo, job_db, data_dir


def _setup_workspace_and_job(job_db: JobQueries) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow')",
            ("ws-1", "Test"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (%s, %s, %s, %s, %s)",
            ("job-1", "ws-1", "demo_workflow", "question", "q-1"),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values (%s, %s, %s)",
            ("job-1", "review_keywords", "pending"),
        )


def test_finish_lease_captures_token_usage(lease_repo):
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)

    workspace_id = "ws-1"
    workflow_key = "demo_workflow"
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
        json.dumps(
            {"model": {"provider": "gateway", "model": "your-model-a"}, "skill_version": "v2"}
        ),
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

    with closing(connect_database(repo.path)) as conn, conn:
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
    workflow_key = "demo_workflow"
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

    with closing(connect_database(repo.path)) as conn, conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is None


def _claim_lease(repo: ExecutorLeaseRepository, job_db: JobQueries, data_dir: Path):
    workspace_id = "ws-1"
    workflow_key = "demo_workflow"
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
    return claimed, workspace_id, node_key


def test_finish_lease_unparseable_events_does_not_fail_lease(lease_repo):
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)
    claimed, workspace_id, node_key = _claim_lease(repo, job_db, data_dir)

    run_dir = data_dir / "jobs" / workspace_id / "job-1" / "runs" / node_key / "broken"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("not json\n{bad json}\n", encoding="utf-8")

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi",),
        log_path="",
        run_dir=str(run_dir.relative_to(data_dir)),
    )

    assert repo.finish(claimed.lease_id, result) is True

    with closing(connect_database(repo.path)) as conn, conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is None


def test_finish_lease_parses_events_outside_write_transaction(lease_repo, monkeypatch):
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)
    claimed, workspace_id, node_key = _claim_lease(repo, job_db, data_dir)

    run_dir = data_dir / "jobs" / workspace_id / "job-1" / "runs" / node_key / "tx-split"
    _write_events(
        run_dir,
        [
            {
                "type": "message_end",
                "message": {"usage": {"input": 5, "output": 3, "cacheRead": 1}},
            }
        ],
    )

    real_parse = token_usage_lease.parse_token_usage_for_lease
    real_persist = token_usage_lease.persist_node_run_usage
    in_transaction_at = {"parse": None, "persist": None}

    def spy_parse(conn, lease_id, data_dir_arg):
        in_transaction_at["parse"] = conn.in_transaction
        return real_parse(conn, lease_id, data_dir_arg)

    def spy_persist(conn, summary):
        in_transaction_at["persist"] = conn.in_transaction
        return real_persist(conn, summary)

    monkeypatch.setattr(token_usage_lease, "parse_token_usage_for_lease", spy_parse)
    monkeypatch.setattr(token_usage_lease, "persist_node_run_usage", spy_persist)

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi",),
        log_path="",
        run_dir=str(run_dir.relative_to(data_dir)),
    )

    assert repo.finish(claimed.lease_id, result) is True

    # The events.jsonl parse must run outside any write transaction; only the
    # short persist step may hold the write lock.
    assert in_transaction_at["parse"] is False
    assert in_transaction_at["persist"] is True

    with closing(connect_database(repo.path)) as conn, conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()

    assert row is not None
    assert row["input_tokens"] == 5
    assert row["output_tokens"] == 3
    assert row["cache_read_tokens"] == 1


def test_claim_lease_rejects_terminal_job(lease_repo):
    """终态作业不得再认领节点：mark 缓存可能因长事务越过 watermark 而滞后
    （mark_scan 文档化缺口），认领事务内必须以当前 jobs.status 为准。"""
    repo, job_db, data_dir = lease_repo
    _setup_workspace_and_job(job_db)
    allocate(job_db, "ws-1", "pi-1", 10)
    bind(job_db, "ws-1", "demo_workflow", "review_keywords", "pi-1")
    with job_db.connect() as conn:
        conn.execute("update jobs set status='failed' where id=%s", ("job-1",))

    claimed = repo.try_claim(
        LeaseClaimRequest(
            executor_id="pi-1",
            global_capacity=10,
            workspace_id="ws-1",
            job_id="job-1",
            workflow_key="demo_workflow",
            node_key="review_keywords",
            capability="review_keywords",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path=str(data_dir / "logs" / "run.log"),
        )
    )

    assert claimed is None
    with job_db.connect() as conn:
        job_row = conn.execute("select status from jobs where id=%s", ("job-1",)).fetchone()
        node_row = conn.execute(
            "select status from job_nodes where job_id=%s and node_key=%s",
            ("job-1", "review_keywords"),
        ).fetchone()
    assert job_row["status"] == "failed"  # 未被认领路径复活为 running
    assert node_row["status"] == "pending"
