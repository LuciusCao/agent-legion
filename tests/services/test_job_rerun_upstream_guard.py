"""rerun eligibility：目标节点存在 failed 上游时拒绝。

回归场景（prod 事故）：A→B→C，B 失败，从 C 批量重跑会把 C 置 pending、
job 置 queued，但 B 保持 failed——调度器要求上游全部 completed，C 永远
不会 ready，job 卡在 queued+failed 死状态。守卫要求这种 rerun 在写入前
以 ``upstream_failed`` 跳过，单 job / batch / preview 三条路径一致。
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services._job_rerun_preview import batch_rerun_preview
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_catalog import WorkflowCatalogService

_NODE_KEYS = ["fetch_questions", "clean_and_parse", "generate_key_info", "review_key_info"]


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        WorkflowCatalogService(settings),
        JobArtifactMutationService(settings.jobs_dir),
    )


def _create_job(job_db, workspace_id: str, source_id: str) -> dict[str, Any]:
    batch = job_db.create_batch(
        "question_comprehension_info",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    return job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=source_id,
        node_keys=_NODE_KEYS,
        workspace_id=workspace_id,
    )


def _seed_workspace(job_db) -> str:
    ws = job_db.create_workspace(
        "upstream-guard", default_workflow_key="question_comprehension_info"
    )
    return str(ws["id"])


def _node_statuses(job_db, job_id: str) -> dict[str, str]:
    return {str(n["node_key"]): str(n["status"]) for n in job_db.list_job_nodes(job_id)}


def test_rerun_rejects_target_with_failed_direct_upstream(rerun_service, job_db):
    ws_id = _seed_workspace(job_db)
    job = _create_job(job_db, ws_id, "Q-stuck")
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="failed")
    job_db.update_job_status(job["id"], "failed", "boom")

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(ws_id, job["id"], "generate_key_info")

    err = exc_info.value
    assert err.status == "skipped"
    assert err.reason_code == "upstream_failed"
    assert "clean_and_parse" in err.message
    # 守卫必须先于任何写入：节点与 job 状态保持原样。
    assert _node_statuses(job_db, job["id"])["generate_key_info"] == "pending"
    assert _node_statuses(job_db, job["id"])["clean_and_parse"] == "failed"
    assert str(job_db.get_job(job["id"])["status"]) == "failed"


def test_rerun_rejects_failed_transitive_upstream(rerun_service, job_db):
    ws_id = _seed_workspace(job_db)
    job = _create_job(job_db, ws_id, "Q-transitive")
    job_db.update_job_node(job["id"], "fetch_questions", status="failed")
    job_db.update_job_status(job["id"], "failed", "boom")

    with pytest.raises(JobOperationError) as exc_info:
        rerun_service.rerun(ws_id, job["id"], "generate_key_info")

    assert exc_info.value.reason_code == "upstream_failed"
    assert "fetch_questions" in exc_info.value.message


def test_rerun_from_failed_node_itself_still_works(rerun_service, job_db):
    ws_id = _seed_workspace(job_db)
    job = _create_job(job_db, ws_id, "Q-rescue")
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="failed")
    job_db.update_job_status(job["id"], "failed", "boom")

    result = rerun_service.rerun(ws_id, job["id"], "clean_and_parse")

    assert result["status"] == "succeeded"
    statuses = _node_statuses(job_db, job["id"])
    assert statuses["clean_and_parse"] == "pending"
    assert statuses["generate_key_info"] == "stale"
    assert statuses["review_key_info"] == "stale"
    assert str(job_db.get_job(job["id"])["status"]) == "queued"


def test_rerun_from_failed_node_mode_unaffected_by_guard(rerun_service, job_db):
    """from_failed_node 救援路径：失败节点的上游必然 completed（它跑过），
    守卫不得误伤。"""
    ws_id = _seed_workspace(job_db)
    job = _create_job(job_db, ws_id, "Q-from-failed")
    job_db.update_job_node(job["id"], "fetch_questions", status="completed")
    job_db.update_job_node(job["id"], "clean_and_parse", status="failed")
    job_db.update_job_status(job["id"], "failed", "boom")

    result = rerun_service.rerun(ws_id, job["id"], from_failed_node=True)

    assert result["status"] == "succeeded"
    assert result["node_key"] == "clean_and_parse"
    assert _node_statuses(job_db, job["id"])["clean_and_parse"] == "pending"


def test_batch_rerun_matches_per_job_for_failed_upstream(rerun_service, job_db):
    ws_id = _seed_workspace(job_db)
    stuck = _create_job(job_db, ws_id, "Q-batch-stuck")
    job_db.update_job_node(stuck["id"], "fetch_questions", status="completed")
    job_db.update_job_node(stuck["id"], "clean_and_parse", status="failed")
    job_db.update_job_status(stuck["id"], "failed", "boom")
    healthy = _create_job(job_db, ws_id, "Q-batch-healthy")
    for key in ("fetch_questions", "clean_and_parse", "generate_key_info"):
        job_db.update_job_node(healthy["id"], key, status="completed")
    job_db.update_job_status(healthy["id"], "failed", "boom")

    results = rerun_service.batch_rerun(
        ws_id, [str(stuck["id"]), str(healthy["id"])], "generate_key_info"
    )

    by_id = {r["job_id"]: r for r in results}
    stuck_result = by_id[str(stuck["id"])]
    assert stuck_result["status"] == "skipped"
    assert stuck_result["reason_code"] == "upstream_failed"
    assert "clean_and_parse" in str(stuck_result["message"])
    assert by_id[str(healthy["id"])]["status"] == "succeeded"


def test_preview_excludes_failed_upstream(rerun_service, job_db):
    ws_id = _seed_workspace(job_db)
    stuck = _create_job(job_db, ws_id, "Q-preview-stuck")
    job_db.update_job_node(stuck["id"], "fetch_questions", status="completed")
    job_db.update_job_node(stuck["id"], "clean_and_parse", status="failed")
    healthy = _create_job(job_db, ws_id, "Q-preview-healthy")
    for key in ("fetch_questions", "clean_and_parse", "generate_key_info"):
        job_db.update_job_node(healthy["id"], key, status="completed")

    preview = batch_rerun_preview(
        rerun_service,
        ws_id,
        [str(stuck["id"]), str(healthy["id"])],
        "generate_key_info",
    )

    assert preview == {"total_count": 2, "eligible_count": 1}
