import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_intake import JobIntakeService


def test_create_batch_requires_existing_active_revision(job_db, settings, agent_manager):
    workspace = job_db.create_workspace("ws-no-revision", default_workflow_key="demo_workflow")
    service = JobIntakeService(
        job_db,
        settings,
        job_event_manager=None,
    )
    with pytest.raises(InvalidOperationError, match="no active workflow revision"):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": "education_video_problems_generation",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
            },
        )


def test_create_batch_compensation_failure_does_not_mask_original_error(
    job_db, settings, agent_manager, monkeypatch, caplog
):
    """#204 窄化：create_jobs_bulk 失败后的 run 行补偿自身失败（DB 连接层
    OSError）只记 warning，原始错误上抛（intake 的 create_batch 不做
    ValueError 换型，原类型保持）。"""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import (
        load_demo_legacy_intake_definition,
        seed_workspace_agent_definitions,
    )

    workspace = job_db.create_workspace(
        "ws-comp", default_workflow_key="education_video_problems_generation"
    )
    seed_workspace_agent_definitions(workspace["id"])
    # The demo workflow retired intake modes (#154); the job-batches intake
    # path needs the legacy variant published as the active revision.
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], load_demo_legacy_intake_definition()
    )
    service = JobIntakeService(job_db, settings, job_event_manager=None)

    def _failing_bulk(**kwargs):
        raise ValueError("Job identity collision for Q1")

    def _failing_discard(run_id: str) -> None:
        raise OSError("db connection reset during compensation")

    monkeypatch.setattr(job_db, "create_jobs_bulk", _failing_bulk)
    monkeypatch.setattr(job_db, "delete_run_without_jobs", _failing_discard)

    with (
        caplog.at_level("WARNING", logger="server.app.services.job_intake"),
        pytest.raises(ValueError, match="Job identity collision"),
    ):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "entity": "question",
                "knowledge_point_ids": ["Q1"],
            },
        )

    assert "left orphaned after job creation failed" in caplog.text


def test_create_batch_compensation_programming_error_propagates(
    job_db, settings, agent_manager, monkeypatch
):
    """#204 窄化：补偿路径的编程错误（TypeError）不再被吞——直接上抛。"""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import (
        load_demo_legacy_intake_definition,
        seed_workspace_agent_definitions,
    )

    workspace = job_db.create_workspace(
        "ws-comp2", default_workflow_key="education_video_problems_generation"
    )
    seed_workspace_agent_definitions(workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], load_demo_legacy_intake_definition()
    )
    service = JobIntakeService(job_db, settings, job_event_manager=None)

    def _failing_bulk(**kwargs):
        raise ValueError("Job identity collision for Q2")

    def _broken_discard(run_id: str) -> None:
        raise TypeError("discard contract violation")

    monkeypatch.setattr(job_db, "create_jobs_bulk", _failing_bulk)
    monkeypatch.setattr(job_db, "delete_run_without_jobs", _broken_discard)

    with pytest.raises(TypeError, match="discard contract violation"):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "entity": "question",
                "knowledge_point_ids": ["Q2"],
            },
        )
