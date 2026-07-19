from __future__ import annotations

from server.app.executors.models import ExecutionResult
from tests.executors.leases.helpers import _claim_request, _setup_workspace


def _finish_and_read_runner(repo, queries, request, result) -> str:
    claim = repo.try_claim(request)
    assert claim is not None
    assert repo.finish(claim.lease_id, result) is True
    with queries.connect() as conn:
        row = conn.execute(
            "select runner from node_runs where id=?", (claim.node_run_id,)
        ).fetchone()
    return row["runner"]


def test_finish_falls_back_to_executor_id(repo_a, queries):
    workspace_id, job_id = _setup_workspace(queries, "ws1", "executor-a", 5)
    request = _claim_request(workspace_id, job_id, executor_id="executor-a")
    runner = _finish_and_read_runner(
        repo_a, queries, request, ExecutionResult(status="completed", exit_code=0)
    )
    assert runner == "executor-a"


def test_finish_persists_explicit_runner(repo_a, queries):
    workspace_id, job_id = _setup_workspace(queries, "ws1", "executor-a", 5)
    request = _claim_request(workspace_id, job_id, executor_id="executor-a")
    runner = _finish_and_read_runner(
        repo_a,
        queries,
        request,
        ExecutionResult(status="completed", exit_code=0, runner="mac-mini-3"),
    )
    assert runner == "mac-mini-3"
