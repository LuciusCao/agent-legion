"""run-to 指定起始节点时，起始节点存在 failed 上游的守卫。

run-to-with-start 与 rerun 共用 mark_nodes_for_rerun 重置语义（只重置
起始节点+下游），存在同样的 queued+failed 死状态隐患，因此共用
_job_rerun_upstream_guard。run-to 不指定起始节点时走 apply_run_to，
会把 closure 内 failed 节点一并重置，无此隐患。
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_operation_error import JobOperationError
from server.app.workflows.registry import load_registered_workflow
from tests.helpers import publish_builtin_revision


@pytest.fixture
def execution_service(job_db: JobQueries, settings):
    return JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
    )


def _create_job(job_db: JobQueries, workspace_id: str, source_id: str) -> dict[str, Any]:
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    definition = load_registered_workflow("education_video_problems_generation")
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id=batch["id"],
        title=source_id,
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
    )


def _node_statuses(job_db: JobQueries, job_id: str) -> dict[str, str]:
    return {str(n["node_key"]): str(n["status"]) for n in job_db.list_job_nodes(job_id)}


def test_run_to_with_start_rejects_failed_upstream(execution_service, job_db):
    ws = job_db.create_workspace(
        "run-to-guard", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, str(ws["id"]))
    job = _create_job(job_db, str(ws["id"]), "Q-run-to-stuck")
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="failed")
    job_db.update_job_status(job["id"], "failed", "boom")
    before = _node_statuses(job_db, job["id"])

    with pytest.raises(JobOperationError) as exc_info:
        execution_service.run_to(
            str(ws["id"]),
            job["id"],
            "publish_content",
            start_node_key="review_script",
        )

    err = exc_info.value
    assert err.status == "skipped"
    assert err.reason_code == "upstream_failed"
    assert "write_script" in err.message
    # 守卫先于任何写入：节点状态与 run-to 控制位均未变。
    assert _node_statuses(job_db, job["id"]) == before
    control = job_db.get_job_execution_control(job["id"])
    assert control["target_node_key"] is None


def test_run_to_with_start_succeeds_when_upstream_healthy(execution_service, job_db):
    ws = job_db.create_workspace(
        "run-to-guard-ok", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, str(ws["id"]))
    job = _create_job(job_db, str(ws["id"]), "Q-run-to-ok")
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="completed")
    job_db.update_job_node(job["id"], "write_script", status="completed")

    result = execution_service.run_to(
        str(ws["id"]),
        job["id"],
        "publish_content",
        start_node_key="review_script",
    )

    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["review_script"] == "pending"
    assert statuses["publish_content"] == "stale"
    control = job_db.get_job_execution_control(job["id"])
    assert control["target_node_key"] == "publish_content"
