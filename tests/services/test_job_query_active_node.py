"""list_jobs 摘要投影与 active node 解析（#1277/#1292/#1294 口径族）。

从 test_job_query_service.py 按被测主题拆出（该文件越过 800 行拆分纪律，
AGENTS.md §4）：本模块只收「列表行 node_summaries / active_node_key 解析」
主题——状态优先级 running → failed → awaiting_approval → ready 与
scheduler summarize_job_status 对齐，同优先级 tie-break 是 job_nodes.id
（插入序），与 job_filtering._ACTIVE_NODE_CLAUSE / facet node_counts
共享同一语义（#1294）。
"""

import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import publish_builtin_revision
from tests.services.job_query_testlib import create_question_job


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db),
    )


def test_list_jobs_returns_typed_node_summaries(query_service, job_db):
    job = create_question_job(job_db, source_id="Q1")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.update_job_node(
        job["id"], "assemble_package", status="failed", error_message="assemble failed"
    )

    listed = query_service.list_jobs(job["workspace_id"])

    assert [node["node_key"] for node in listed[0]["node_summaries"]] == [
        "question_understanding",
        "assemble_package",
    ]
    assert listed[0]["completed_nodes"] == 1
    assert listed[0]["total_nodes"] == 2
    assert listed[0]["active_node_key"] == "assemble_package"
    assert listed[0]["error_summary"] == "assemble failed"
    assert listed[0]["execution_control"] == {
        "mode": "full",
        "target_node_key": None,
        "paused": False,
        "pause_reason": "",
    }


def test_list_jobs_active_node_key_parks_on_approval_gate(query_service, job_db):
    # #1277：停在大流程审批门时 active_node_key 是门本身，不再是 None
    # （此前列表行回退到下游首个 pending）。
    job = create_question_job(job_db, source_id="Q-approval")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.update_job_node(job["id"], "assemble_package", status="awaiting_approval")

    listed = query_service.list_jobs(job["workspace_id"])

    assert listed[0]["active_node_key"] == "assemble_package"
    assert listed[0]["error_summary"] == ""


def test_list_jobs_active_node_key_picks_ready_node(query_service, job_db):
    job = create_question_job(job_db, source_id="Q-ready")
    job_db.update_job_node(job["id"], "question_understanding", status="completed")
    job_db.update_job_node(job["id"], "assemble_package", status="ready")

    listed = query_service.list_jobs(job["workspace_id"])

    assert listed[0]["active_node_key"] == "assemble_package"


def test_list_jobs_active_node_key_failed_outranks_approval_gate(query_service, job_db):
    # 并行分支「一边 awaiting_approval、一边 failed」：failed 保持既有优先，
    # 与 scheduler summarize_job_status 的 job 级状态推导一致（#1277）。
    job = create_question_job(job_db, source_id="Q-parallel")
    job_db.update_job_node(job["id"], "question_understanding", status="awaiting_approval")
    job_db.update_job_node(job["id"], "assemble_package", status="failed", error_message="boom")

    listed = query_service.list_jobs(job["workspace_id"])

    assert listed[0]["active_node_key"] == "assemble_package"
    assert listed[0]["error_summary"] == "boom"


def test_list_jobs_active_node_key_tiebreak_uses_node_insertion_order(query_service, job_db):
    # #1294：同优先级节点的 tie-break 是 job_nodes.id（插入序），与
    # job_filtering._ACTIVE_NODE_CLAUSE / facet node_counts 共享同一语义。
    # 声明序（拓扑序）a 在 b 前，但 b 先插入（id 更小）——active 节点取 b。
    workspace = job_db.create_workspace("tiebreak")
    definition = WorkflowDefinition(
        key="tiebreak",
        label="Tie Break",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(key="b", label="B", capability="cap_b"),
        },
    )
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace["id"], definition)
    job = job_db.create_job(
        workflow_key="tiebreak",
        source_type="question",
        source_id="Q-tiebreak",
        run_id="",
        title="Tiebreak",
        node_keys=["b", "a"],
        workspace_id=workspace["id"],
    )
    job_db.update_job_node(job["id"], "a", status="failed")
    job_db.update_job_node(job["id"], "b", status="failed")

    listed = query_service.list_jobs(workspace["id"])

    assert [node["node_key"] for node in listed[0]["node_summaries"]] == ["a", "b"]
    assert listed[0]["active_node_key"] == "b"


def test_list_jobs_orders_node_summaries_by_workflow_dag(query_service, job_db):
    workspace = job_db.create_workspace("default")
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=[
            "publish_content",
            "review_questions",
            "generate_questions",
            "review_script",
            "write_script",
            "intake_knowledge_points",
        ],
        workspace_id=workspace["id"],
    )

    listed = query_service.list_jobs(job["workspace_id"])

    node_keys = [node["node_key"] for node in listed[0]["node_summaries"]]
    # Ordering is topological over the workspace's ACTIVE revision (schema
    # v50); ready-ties break by the stored definition's node order (canonical
    # JSON, alphabetical) — identical to what intake and Studio already use.
    assert node_keys[:3] == [
        "intake_knowledge_points",
        "generate_questions",
        "review_questions",
    ]
    assert node_keys.index("publish_content") > node_keys.index("review_questions")
