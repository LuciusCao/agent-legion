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


def test_create_batch_chunk_failure_keeps_partial_run_failed(job_db, settings, agent_manager):
    """codex 轮 1 #2：sync /job-batches 的分块失败协议与 /runs 对齐。

    >1000 候选且 chunk 2 失败时（DB 触发器使 chunk 2 首行必败——真路径
    INSERT 失败，非 monkeypatch）：已提交 chunk 保留、run 行落 failed 携
    进度（不再停留 'created' 无痕迹）、异常为 PartialRunCreationError
    （路由映射 400 结构化 detail）；首块前失败照旧删 run 行。
    """
    from server.app.services.run_partial_failure import PartialRunCreationError
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import (
        load_demo_legacy_intake_definition,
        seed_workspace_agent_definitions,
    )

    workspace = job_db.create_workspace("ws-chunk-fail", default_workflow_key="demo_workflow")
    seed_workspace_agent_definitions(workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], load_demo_legacy_intake_definition()
    )
    service = JobIntakeService(job_db, settings, job_event_manager=None)

    with job_db.connect() as conn:
        conn.execute("drop trigger if exists jobs_poison_guard on jobs")
        conn.execute("drop function if exists jobs_poison_reject()")
        conn.execute(
            "create function jobs_poison_reject() returns trigger as $$"
            " begin"
            "  if new.source_id like 'poison%' then"
            "   raise exception 'poison row rejected by test guard';"
            "  end if;"
            "  return new;"
            " end $$ language plpgsql"
        )
        conn.execute(
            "create trigger jobs_poison_guard before insert on jobs"
            " for each row execute function jobs_poison_reject()"
        )

    values = [f"Q{i:04d}" for i in range(1000)] + ["poison-0"]
    try:
        with pytest.raises(PartialRunCreationError) as caught:
            service.create_batch(
                workspace["id"],
                {
                    "workflow_key": workspace["id"],
                    "source_kind": "direct_ids",
                    "entity": "question",
                    "knowledge_point_ids": values,
                },
            )
    finally:
        with job_db.connect() as conn:
            conn.execute("drop trigger if exists jobs_poison_guard on jobs")
            conn.execute("drop function if exists jobs_poison_reject()")

    assert caught.value.created_so_far == 1000
    with job_db.connect() as conn:
        run = conn.execute(
            "select status, error_message, created_count from runs where id=%s",
            (caught.value.run_id,),
        ).fetchone()
        jobs = conn.execute(
            "select count(*) as n from jobs where run_id=%s", (caught.value.run_id,)
        ).fetchone()
    assert str(run["status"]) == "failed"
    assert "1000 job(s) were already created" in str(run["error_message"])
    assert int(run["created_count"]) == 1000
    assert int(jobs["n"]) == 1000


def test_create_batch_before_first_chunk_failure_deletes_run(
    job_db, settings, agent_manager, monkeypatch
):
    """codex 轮 1 #2 的另一支：首块前失败（零 job 提交）照旧删除 run 行——
    与旧单事务语义一致，不产生 PartialRunCreationError 的失败现场。"""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import (
        load_demo_legacy_intake_definition,
        seed_workspace_agent_definitions,
    )

    workspace = job_db.create_workspace("ws-empty-fail", default_workflow_key="demo_workflow")
    seed_workspace_agent_definitions(workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], load_demo_legacy_intake_definition()
    )
    service = JobIntakeService(job_db, settings, job_event_manager=None)

    def _failing_bulk(**kwargs):
        raise ValueError("simulated pre-chunk failure")

    monkeypatch.setattr(job_db, "create_jobs_bulk", _failing_bulk)
    with pytest.raises(ValueError, match="simulated pre-chunk failure"):
        service.create_batch(
            workspace["id"],
            {
                "workflow_key": workspace["id"],
                "source_kind": "direct_ids",
                "entity": "question",
                "knowledge_point_ids": ["Q1"],
            },
        )

    with job_db.connect() as conn:
        runs = conn.execute(
            "select count(*) as n from runs where workspace_id=%s", (workspace["id"],)
        ).fetchone()
    assert int(runs["n"]) == 0
