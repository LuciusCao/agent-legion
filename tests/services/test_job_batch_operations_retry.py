"""Retry-failed requeue service semantics (#1358): terminal-only, kind matrix,
failed-set derivation, provenance, and feeder execution of the retry op.

Drives ``submit_retry_failed`` against real op rows (submitted via the same
``submit_operation`` the accept path uses, transitioned terminal via the
guarded CAS); the end-to-end arm drains ``JobBatchOpsFeeder`` so the retry
op's per-job results are observed through the production execution path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.services.job_batch_operations import (
    BatchOpsExecutors,
    JobBatchOpsFeeder,
    submit_operation,
    submit_retry_failed,
)
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings
from tests.helpers import publish_builtin_revision

WORKSPACE = "batch-retry-ws"
NODE_KEYS = ["intake_knowledge_points", "write_script", "review_script", "publish_content"]


@pytest.fixture
def workspace(job_db: JobQueries) -> str:
    created = job_db.create_workspace(WORKSPACE)
    publish_builtin_revision(job_db, created["id"])
    return str(created["id"])


def _terminal_op(
    job_db: JobQueries,
    kind: str,
    ids: list[str],
    params: dict[str, Any] | None = None,
    *,
    failed: list[str] | None = None,
    status: str = "completed",
) -> str:
    """One op row + per-job result rows (failed set parameterized), terminal."""
    op = submit_operation(job_db, WORKSPACE, kind, ids, params or {}, "user:tester")
    op_id = str(op["id"])
    failed_set = set(failed or [])
    rows = [
        {
            "seq": seq,
            "job_id": job_id,
            "status": "failed" if job_id in failed_set else "succeeded",
            "reason_code": "not_found" if job_id in failed_set else None,
            "message": None,
            "payload": {},
        }
        for seq, job_id in enumerate(ids)
    ]
    job_db.insert_job_batch_operation_results(op_id, rows)
    transitioned = job_db.transition_job_batch_operation(
        op_id, from_statuses=("pending", "running"), to_status=status
    )
    assert transitioned is not None
    return op_id


def test_retry_failed_requeues_failed_items_with_original_kind_and_params(
    workspace: str, job_db: JobQueries
) -> None:
    op_id = _terminal_op(
        job_db,
        "rerun",
        ["j-ok", "j-bad-1", "j-skip", "j-bad-2"],
        {"node_key": "write_script", "from_failed_node": True},
        failed=["j-bad-1", "j-bad-2"],
    )
    # 一笔 skipped 结果只进跳过集，不进重试选择。
    job_db.insert_job_batch_operation_results(
        op_id,
        [
            {
                "seq": 4,
                "job_id": "j-skipped",
                "status": "skipped",
                "reason_code": "terminal",
                "message": None,
                "payload": {},
            }
        ],
    )

    retry = submit_retry_failed(job_db, WORKSPACE, op_id, created_by="user:retryer")

    assert retry["status"] == "pending"
    assert retry["kind"] == "rerun"
    assert retry["items_total"] == 2
    assert retry["created_by"] == "user:retryer"
    spec = json.loads(str(retry["target_spec_json"]))
    # 失败集 distinct、按首败 seq 排序；原参数逐字冻结；来源可追溯。
    assert spec["job_ids"] == ["j-bad-1", "j-bad-2"]
    assert spec["params"] == {"node_key": "write_script", "from_failed_node": True}
    assert spec["retry_of"] == op_id
    # 原 op 不被改写（重试只读它）。
    original = job_db.get_job_batch_operation(op_id)
    assert original["status"] == "completed"
    assert "retry_of" not in json.loads(str(original["target_spec_json"]))


def test_retry_failed_dedups_positional_kinds(workspace: str, job_db: JobQueries) -> None:
    """run_to 的 frozen 选择保留重复 id（positional 映射）；同一 job 在两个
    seq 上都 failed 时，重试选择只含一次。"""
    op_id = _terminal_op(job_db, "run_to", ["j-1", "j-1", "j-2"], failed=["j-1"])

    retry = submit_retry_failed(job_db, WORKSPACE, op_id, created_by="user:tester")

    spec = json.loads(str(retry["target_spec_json"]))
    assert spec["job_ids"] == ["j-1"]


@pytest.mark.parametrize("status", ["pending", "running", "paused"])
def test_retry_failed_refuses_non_terminal(workspace: str, job_db: JobQueries, status: str) -> None:
    op = submit_operation(job_db, WORKSPACE, "rerun", ["j-1"], {}, "user:tester")
    if status != "pending":
        job_db.transition_job_batch_operation(
            str(op["id"]), from_statuses=("pending", "running"), to_status=status
        )
    with pytest.raises(ConflictError, match="not terminal"):
        submit_retry_failed(job_db, WORKSPACE, str(op["id"]), created_by="user:tester")


@pytest.mark.parametrize("kind", ["pause", "resume"])
def test_retry_failed_refuses_meaningless_kinds(
    workspace: str, job_db: JobQueries, kind: str
) -> None:
    op_id = _terminal_op(job_db, kind, ["j-1"], failed=["j-1"])
    with pytest.raises(InvalidOperationError, match="does not support retry-failed"):
        submit_retry_failed(job_db, WORKSPACE, op_id, created_by="user:tester")


def test_retry_failed_refuses_when_nothing_failed(workspace: str, job_db: JobQueries) -> None:
    op_id = _terminal_op(job_db, "delete", ["j-1"])
    with pytest.raises(InvalidOperationError, match="no failed items"):
        submit_retry_failed(job_db, WORKSPACE, op_id, created_by="user:tester")


def test_retry_failed_404_unknown_and_cross_workspace(workspace: str, job_db: JobQueries) -> None:
    op_id = _terminal_op(job_db, "delete", ["j-1"], failed=["j-1"])
    with pytest.raises(NotFoundError):
        submit_retry_failed(job_db, WORKSPACE, "op-missing", created_by="user:tester")
    other = job_db.create_workspace("batch-retry-other")
    with pytest.raises(NotFoundError):
        submit_retry_failed(job_db, str(other["id"]), op_id, created_by="user:tester")


def test_retry_failed_op_executes_through_feeder(
    workspace: str, job_db: JobQueries, settings: Settings
) -> None:
    """端到端：重试 op 走同一 feeder，per-job 语义与同步路径一致（rerun 一
    个 failed job 成功、missing job 再次 not_found）；原 op 结果不变。"""
    failed_job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="RT-1",
        run_id="",
        title="RT-1",
        node_keys=list(NODE_KEYS),
        workspace_id=WORKSPACE,
    )
    job_db.update_job_status(failed_job["id"], "failed")
    job_id = str(failed_job["id"])
    op_id = _terminal_op(
        job_db,
        "rerun",
        [job_id, "missing-job"],
        {"node_key": "write_script", "from_failed_node": False},
        failed=[job_id, "missing-job"],
    )

    retry = submit_retry_failed(job_db, WORKSPACE, op_id, created_by="user:tester")
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    feeder = JobBatchOpsFeeder(
        job_db,
        settings,
        BatchOpsExecutors(
            rerun=services.rerun,
            deletion=services.deletion,
            execution=services.execution,
            pause=services.pause,
            upgrade=services.workflow_upgrade,
            packages=JobPackageService(job_db, settings),
        ),
    )
    while feeder.consume_once():
        pass

    retry_op = job_db.get_job_batch_operation(str(retry["id"]))
    assert retry_op["status"] == "completed"
    assert (retry_op["succeeded_count"], retry_op["failed_count"]) == (1, 1)
    results = {
        str(row["job_id"]): json.loads(str(row["payload_json"]))
        for row in job_db.list_job_batch_operation_results_page(str(retry["id"]), limit=10)
    }
    assert results[job_id]["status"] == "succeeded"
    assert results[job_id]["node_key"] == "write_script"
    assert results["missing-job"]["reason_code"] == "not_found"
    # 幂等面：重试不重写原 op（结果集冻结于终态）。
    original = job_db.get_job_batch_operation(op_id)
    assert original["status"] == "completed"
    assert len(job_db.list_job_batch_operation_results_page(op_id, limit=10)) == 2
