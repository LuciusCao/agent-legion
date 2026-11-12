from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.app.executors._lease_claims import claim_lease
from server.app.executors.leases import ExecutorLeaseRepository, database_timestamp
from server.app.executors.models import (
    ConfigurationFailureRequest,
    ExecutionResult,
)
from server.app.jobs import JobQueries
from server.app.storage_paths import ManagedPathError
from tests.executors.leases.helpers import (
    _claim_request,
    _setup_workspace,
)
from tests.helpers.job_dirs import make_job_dir


def test_claim_lease_persists_relative_log_path(queries: JobQueries) -> None:
    data_dir = queries.jobs_dir.parent
    log_path = data_dir / "logs" / "jobs" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-log", "code-default", workspace_limit=2
    )
    request = _claim_request(
        workspace_id,
        job_id,
        executor_id="code-default",
        global_capacity=2,
        log_path=str(log_path),
    )

    with queries.connect() as conn:
        claimed = claim_lease(conn, request, data_dir)
        conn.commit()

    assert claimed is not None
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/jobs/run.log"


def test_finish_lease_persists_relative_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-finish", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    absolute_log = data_dir / "logs" / "updated.log"
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        log_path=str(absolute_log),
    )
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/updated.log"


def test_fail_without_lease_persists_relative_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-fail", "code-default", workspace_limit=2
    )
    data_dir = queries.jobs_dir.parent
    absolute_log = data_dir / "logs" / "error.log"
    request = ConfigurationFailureRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="demo_workflow",
        node_key="review_keywords",
        capability="review_keywords",
        log_path=str(absolute_log),
    )
    run_id = repo_a.fail_without_lease(request, "missing binding")
    assert run_id is not None

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/error.log"


def test_try_claim_returns_absolute_log_path_and_persists_relative(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-repo-claim-abs", "code-default", workspace_limit=2
    )
    data_dir = queries.jobs_dir.parent
    absolute_log = data_dir / "logs" / "jobs" / "run.log"
    absolute_log.parent.mkdir(parents=True, exist_ok=True)
    request = _claim_request(
        workspace_id,
        job_id,
        executor_id="code-default",
        global_capacity=2,
        log_path=str(absolute_log),
    )

    claim = repo_a.try_claim(request)

    assert claim is not None
    assert claim.log_path == str(absolute_log)
    with queries.connect() as conn:
        run = conn.execute(
            "select * from node_runs where job_id=%s and node_key=%s",
            (job_id, "review_keywords"),
        ).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/jobs/run.log"


def test_finish_lease_canonicalizes_fallback_legacy_absolute_log_path(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-finish-fallback", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    legacy_absolute_log = data_dir / "logs" / "legacy.log"
    legacy_absolute_log.parent.mkdir(parents=True, exist_ok=True)
    with queries.connect() as conn:
        conn.execute(
            "update node_runs set log_path=%s where id=%s",
            (str(legacy_absolute_log), claim.node_run_id),
        )
        conn.execute("commit")

    result = ExecutionResult(status="completed", exit_code=0, log_path="")
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["log_path"] == "logs/legacy.log"


def test_finish_lease_persists_relative_session_dir(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-rel-session", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    absolute_session = (
        data_dir / "jobs" / workspace_id / job_id / "runs" / "node" / "abc" / "session"
    )
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        session_dir=str(absolute_session),
    )
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute("select * from node_runs where id=%s", (claim.node_run_id,)).fetchone()
    assert run is not None
    assert run["session_dir"] == absolute_session.relative_to(data_dir).as_posix()


def test_finish_lease_rejects_session_dir_outside_jobs(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-invalid-session", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    result = ExecutionResult(
        status="completed",
        exit_code=0,
        session_dir="sessions/abc",
    )

    with pytest.raises(ManagedPathError, match="expected 'jobs'"):
        repo_a.finish(claim.lease_id, result)


def test_database_timestamp_is_utc_without_t_separator() -> None:
    now = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert database_timestamp(now) == "2025-01-02 03:04:05.123456"


def test_finish_lease_derives_run_dir_via_job_storage_dir(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """Empty result.run_dir falls back to probing only the job's known dirs."""
    workspace_id, job_id = _setup_workspace(
        queries, "ws-derive-storage", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    token_dir = make_job_dir(data_dir, workspace_id, job_id) / "runs" / "review_keywords" / "tok-1"
    token_dir.mkdir(parents=True)

    result = ExecutionResult(status="completed", exit_code=0)
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute(
            "select run_dir from node_runs where id=%s", (claim.node_run_id,)
        ).fetchone()
    assert run is not None
    assert run["run_dir"] == token_dir.relative_to(data_dir).as_posix()


def test_finish_lease_derives_run_dir_via_legacy_flat_layout(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """A pre-sharding row (storage_dir cleared) still finds flat-layout runs."""
    workspace_id, job_id = _setup_workspace(
        queries, "ws-derive-legacy", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    with queries.connect() as conn:
        conn.execute("update jobs set storage_dir='' where id=%s", (job_id,))
        conn.commit()
    token_dir = (
        make_job_dir(data_dir, workspace_id, job_id, sharded=False)
        / "runs"
        / "review_keywords"
        / "tok-legacy"
    )
    token_dir.mkdir(parents=True)

    result = ExecutionResult(status="completed", exit_code=0)
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute(
            "select run_dir from node_runs where id=%s", (claim.node_run_id,)
        ).fetchone()
    assert run is not None
    assert run["run_dir"] == token_dir.relative_to(data_dir).as_posix()


def test_finish_lease_transfers_run_dir_ownership_from_older_rows(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """#1315：finish 写 run_dir=D 时同事务缴清其他行对 D 的认领——一个磁盘
    目录同一时刻至多被一个 node_runs 行拥有。worker agent 节点的固定 token
    目录（runs/<node>/worker）被历次 run 复用；不转移的话，共享认领的历史
    行一过 retention，sweep 非空分支就会删掉当前 run 正在使用的目录。"""
    workspace_id, job_id = _setup_workspace(
        queries, "ws-run-dir-transfer", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    data_dir = queries.jobs_dir.parent
    token_dir = make_job_dir(data_dir, workspace_id, job_id) / "runs" / "review_keywords" / "worker"
    token_dir.mkdir(parents=True)
    run_dir_rel = token_dir.relative_to(data_dir).as_posix()

    # 历史行（上一轮 run 的残留认领）：与本次 finish 共享同一固定目录。
    with queries.connect() as conn:
        conn.execute(
            "insert into node_runs(job_id, node_key, status, run_dir, session_dir, finished_at)"
            " values (%s, %s, 'completed', %s, %s, current_timestamp)",
            (job_id, "review_keywords", run_dir_rel, f"{run_dir_rel}/session"),
        )
        conn.commit()

    result = ExecutionResult(status="completed", exit_code=0)
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        rows = conn.execute(
            "select id, run_dir, session_dir from node_runs"
            " where job_id=%s and node_key=%s order by id",
            (job_id, "review_keywords"),
        ).fetchall()
    owners = [row for row in rows if row["run_dir"] == run_dir_rel]
    assert [row["id"] for row in owners] == [claim.node_run_id]
    stale = [row for row in rows if row["id"] != claim.node_run_id]
    assert stale
    assert all(row["run_dir"] == "" and row["session_dir"] == "" for row in stale)


def test_finish_lease_without_run_tokens_keeps_empty_run_dir(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """Agent-style results (no run tokens on disk) stay empty without scanning."""
    workspace_id, job_id = _setup_workspace(
        queries, "ws-derive-empty", "code-default", workspace_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(workspace_id, job_id, executor_id="code-default", global_capacity=2)
    )
    assert claim is not None

    make_job_dir(queries.jobs_dir.parent, workspace_id, job_id)  # no runs/

    result = ExecutionResult(status="completed", exit_code=0)
    assert repo_a.finish(claim.lease_id, result) is True

    with queries.connect() as conn:
        run = conn.execute(
            "select run_dir from node_runs where id=%s", (claim.node_run_id,)
        ).fetchone()
    assert run is not None
    assert run["run_dir"] == ""
