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
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowShardSpec
from server.app.workflows.sharding import materialize_shards
from tests.helpers import publish_builtin_revision
from tests.helpers.executor_worker import make_definition


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))


@pytest.fixture
def job(job_db) -> Iterator[dict]:
    workspace = job_db.create_workspace("default")
    publish_builtin_revision(job_db, workspace["id"])
    created = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Question 1",
        node_keys=["write_script", "review_script"],
        workspace_id=workspace["id"],
    )
    yield created
    HYDRATION_DEFER_BOARD.publish(created["id"], ())


def test_detail_without_notice_has_no_defer(query_service, job) -> None:
    detail = query_service.detail(job["id"])
    assert all(node["hydration_defer"] is None for node in detail["nodes"])


def test_detail_projects_notice_onto_waiting_node_only(query_service, job_db, job) -> None:
    job_db.update_job_node(job["id"], "write_script", status="completed")
    HYDRATION_DEFER_BOARD.publish(
        job["id"],
        [
            HydrationDeferNotice(
                input_name="script.json",
                outcome="object_missing",
                rerun_nodes=("write_script",),
                waiting_nodes=("review_script", "write_script"),
            )
        ],
    )

    nodes = {n["node_key"]: n for n in query_service.detail(job["id"])["nodes"]}

    assert nodes["review_script"]["hydration_defer"] == {
        "inputs": ["script.json"],
        "reasons": ["object_missing"],
        "rerun_nodes": ["write_script"],
    }
    # 已完成节点不是「等待中」：即使公告列了它也不投影。
    assert nodes["write_script"]["hydration_defer"] is None


def test_detail_skips_nodes_outside_run_to_closure(query_service, job_db, job) -> None:
    """codex #1018 R2：until_node 闭包外的节点本就不调度，不算被 defer 挡住。"""
    job_db.set_job_execution_mode(job["id"], "until_node", target_node_key="write_script")
    HYDRATION_DEFER_BOARD.publish(
        job["id"],
        [
            HydrationDeferNotice(
                input_name="input.json",
                outcome="hash_mismatch",
                rerun_nodes=("write_script",),
                waiting_nodes=("review_script", "write_script"),
            )
        ],
    )

    nodes = {n["node_key"]: n for n in query_service.detail(job["id"])["nodes"]}

    assert nodes["write_script"]["hydration_defer"] is not None
    assert nodes["review_script"]["hydration_defer"] is None


def _shard_job(query_service, job_db, job, monkeypatch, *, pending_shards: bool) -> None:
    """write_script 改为分片节点，DB 状态 running；两个 shard 是否仍有 pending 由参数定。"""
    definition = make_definition(
        [
            WorkflowNode(
                key="write_script",
                label="write",
                capability="write",
                outputs=["script.json"],
                shard=WorkflowShardSpec(count=2),
            ),
            WorkflowNode(
                key="review_script",
                label="review",
                capability="review",
                inputs=["script.json"],
                after=["write_script"],
            ),
        ]
    )
    monkeypatch.setattr(query_service, "_definition_for_job", lambda _job: definition)
    job_db.update_job_node(job["id"], "write_script", status="running")
    with job_db.connect() as conn:
        materialize_shards(conn, job["id"], "write_script", [{"i": 0}, {"i": 1}], max_shards=4)
        # 扇出进行中：shard 0 已派发；有 pending 时 shard 1 仍待领取，否则也已派发。
        conn.execute(
            "update node_shards set status='running' where job_id=%s and node_key=%s"
            " and shard_index <= %s",
            (job["id"], "write_script", 0 if pending_shards else 1),
        )
    HYDRATION_DEFER_BOARD.publish(
        job["id"],
        [
            HydrationDeferNotice(
                input_name="source.json",
                outcome="object_missing",
                rerun_nodes=("intake",),
                waiting_nodes=("review_script", "write_script"),
            )
        ],
    )


def test_detail_projects_running_shard_node_with_pending_shards(
    query_service, job_db, job, monkeypatch
) -> None:
    """#1021：running + pending shard 按分片有效状态（pending）投影，与 worker 口径一致。"""
    _shard_job(query_service, job_db, job, monkeypatch, pending_shards=True)

    nodes = {n["node_key"]: n for n in query_service.detail(job["id"])["nodes"]}

    assert nodes["write_script"]["status"] == "running"
    assert nodes["write_script"]["hydration_defer"] == {
        "inputs": ["source.json"],
        "reasons": ["object_missing"],
        "rerun_nodes": ["intake"],
    }


def test_detail_skips_running_shard_node_without_pending_shards(
    query_service, job_db, job, monkeypatch
) -> None:
    """#1021：shard 已全部派发的 running 分片节点不是「等待中」，不投影。"""
    _shard_job(query_service, job_db, job, monkeypatch, pending_shards=False)

    nodes = {n["node_key"]: n for n in query_service.detail(job["id"])["nodes"]}

    assert nodes["write_script"]["hydration_defer"] is None
