"""Ready-gate hydration 的悬挂清单行兜底（#827）。

「恢复不全 → 不缓存、下轮重试」在对象永久缺失 / 字节不符时退化为无声
永久 defer：整个 job 的评估被挡住，连会重写该名字的上游生产者都派发不
出去。兜底（``workflow_worker/hydration_dangling.py``）：同一清单行连续
悬挂 ``DANGLING_ESCALATION_PASSES`` 轮后——在途生产者会重写的名字释放出
defer 集（job 继续调度），否则继续 defer 但打带 suggested action 的
WARNING；pass log 区分「调度暂停」与「hydration 恢复不全」。
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker.hydration_dangling import (
    DANGLING_ESCALATION_PASSES,
    rewrite_pending,
)
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.ready_gate_hydration import A_PAYLOAD, seed_manifest_row
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                inputs=["a_out.json"],
                outputs=["b_out.json"],
            ),
        },
    )


def _job(queries: JobQueries, workspace: dict, a_status: str, b_status: str) -> dict:
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status=a_status)
    queries.update_job_node(job["id"], "b", status=b_status)
    return job


@pytest.mark.postgres
def test_dangling_row_with_in_flight_producer_is_released_after_n_passes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#827 现场形态：全量重跑后 a 在途（pending）、b stale，a_out.json 的清单
    行指向已删对象。修复前 job 永久 defer、a 永不派发；兜底后第 N 轮释放，
    a 被 claim（它会重写该名字）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    job = _job(queries, workspace, "pending", "stale")
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)  # 对象不存在
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "a")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [_definition()], artifact_object_store=store
    )

    with caplog.at_level(logging.WARNING):
        for _ in range(DANGLING_ESCALATION_PASSES - 1):
            worker._poll()
            # 前 N-1 轮维持既有纪律：defer、不缓存、不派发。
            assert queries.get_job_node(job["id"], "a")["status"] == "pending"
            assert job["id"] not in worker.state.job_evals
            assert worker.state.pass_skips["hydration_deferred"] == 1
            assert worker.state.pass_skips["paused_jobs"] == 0
        assert "reason=hydration_incomplete" in caplog.text
        assert "object_missing" in caplog.text

        worker._poll()

    assert queries.get_job_node(job["id"], "a")["status"] == "running"
    assert worker.state.pass_skips["hydration_deferred"] == 0
    released = [r for r in caplog.records if "consecutive passes" in r.getMessage()]
    assert len(released) == 1
    assert "treating it as absent" in released[0].getMessage()

    executor.block_event.set()
    worker.stop()


@pytest.mark.postgres
def test_dangling_row_without_rewriter_stays_deferred_with_suggested_action(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """没有在途生产者会重写（a 已 completed）且字节不符：不猜，继续 defer，
    但升级为带 suggested action 的 WARNING（每个清单行只打一次）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    job = _job(queries, workspace, "completed", "pending")
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: b"someone else's bytes"})
    )
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        RecordingExecutor("code"),
        [_definition()],
        artifact_object_store=store,
    )

    with caplog.at_level(logging.WARNING):
        for _ in range(DANGLING_ESCALATION_PASSES + 2):
            worker._poll()

    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert job["id"] not in worker.state.job_evals
    assert not (resolve_job_dir(job, queries.jobs_dir) / "a_out.json").exists()
    escalations = [r.getMessage() for r in caplog.records if "consecutive passes" in r.getMessage()]
    assert len(escalations) == 1
    assert "hash_mismatch" in escalations[0]
    assert "suggested action: rerun producer node(s) ['a']" in escalations[0]
    worker.stop()


@pytest.mark.postgres
def test_new_manifest_row_restarts_the_streak(tmp_path: Path) -> None:
    """行身份变化（新写者重新登记）即重新计数；恢复成功后计数清除。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    job = _job(queries, workspace, "completed", "pending")
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        RecordingExecutor("code"),
        [_definition()],
        artifact_object_store=store,
    )
    streaks = worker.state.hydration_dangling

    worker._poll()
    worker._poll()
    assert streaks.describe(job["id"]) == {
        "a_out.json": f"object_missing 2/{DANGLING_ESCALATION_PASSES}"
    }
    # 新写者登记：同名行换了内容哈希（对象随之落地）→ 计数从头开始。
    fresh = b'{"from": "a", "v": 2}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update job_artifacts set content_hash=%s, size_bytes=%s where job_id=%s",
            ("deadbeef", len(fresh), job["id"]),
        )
    worker._poll()
    assert streaks.describe(job["id"]) == {
        "a_out.json": f"object_missing 1/{DANGLING_ESCALATION_PASSES}"
    }
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update job_artifacts set content_hash=%s where job_id=%s",
            (hashlib.sha256(fresh).hexdigest(), job["id"]),
        )
    storage.objects[storage_key] = fresh
    worker._poll()
    assert streaks.describe(job["id"]) == {}
    worker.stop()


@pytest.mark.no_db
def test_rewrite_pending_requires_every_runnable_consumer_barriered() -> None:
    definition = _definition()
    # b 被在途的 a 挡住 → 可释放。
    assert rewrite_pending(definition, {"a": "pending", "b": "stale"}, "a_out.json")
    # a 已完成：没有在途生产者会重写 → 不可释放。
    assert not rewrite_pending(definition, {"a": "completed", "b": "pending"}, "a_out.json")
    # 纯 RMW：唯一生产者是消费者自己 → 自己需要启动输入，不可释放。
    rmw = WorkflowDefinition(
        key="t",
        label="T",
        intake=WorkflowIntake(),
        nodes={
            "r": WorkflowNode(
                key="r", label="R", capability="cap_r", inputs=["x.json"], outputs=["x.json"]
            )
        },
    )
    assert not rewrite_pending(rmw, {"r": "pending"}, "x.json")


@pytest.mark.no_db
def test_rewrite_pending_never_releases_condition_artifacts() -> None:
    """条件产物缺失会被当成条件为假（分支被静默标 not_applicable），恒不释放。"""
    definition = WorkflowDefinition(
        key="t",
        label="T",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["v.json"]),
            "b": WorkflowNode(
                key="b", label="B", capability="cap_b", inputs=["v.json"], after=["a"]
            ),
        },
        edges=[
            WorkflowEdge(
                source="a",
                target="b",
                condition=WorkflowCondition(artifact="v.json", path="ok", equals=True),
            )
        ],
    )
    assert not rewrite_pending(definition, {"a": "pending", "b": "pending"}, "v.json")
