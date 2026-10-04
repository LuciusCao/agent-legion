"""job 详情的 hydration defer 投影（#887）：只在确有公告时、只给等待中的受阻节点。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from server.app.services.hydration_defer_board import (
    HYDRATION_DEFER_BOARD,
    HydrationDeferNotice,
)
from server.app.services.job_queries import JobQueryService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from tests.helpers import publish_builtin_revision


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))


@pytest.fixture
def job(job_db) -> Iterator[dict]:
    workspace = job_db.create_workspace(
        "default", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    created = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Question 1",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    yield created
    HYDRATION_DEFER_BOARD.publish(created["id"], ())


def test_detail_without_notice_has_no_defer(query_service, job) -> None:
    detail = query_service.detail(job["id"])
    assert all(node["hydration_defer"] is None for node in detail["nodes"])


def test_detail_projects_notice_onto_waiting_node_only(query_service, job_db, job) -> None:
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    HYDRATION_DEFER_BOARD.publish(
        job["id"],
        [
            HydrationDeferNotice(
                input_name="understanding.json",
                outcome="object_missing",
                rerun_nodes=("question_understanding",),
                waiting_nodes=("assemble_package", "question_understanding"),
            )
        ],
    )

    nodes = {n["node_key"]: n for n in query_service.detail(job["id"])["nodes"]}

    assert nodes["assemble_package"]["hydration_defer"] == {
        "inputs": ["understanding.json"],
        "reasons": ["object_missing"],
        "rerun_nodes": ["question_understanding"],
    }
    # 已完成节点不是「等待中」：即使公告列了它也不投影。
    assert nodes["question_understanding"]["hydration_defer"] is None
