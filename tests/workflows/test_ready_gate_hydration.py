"""Ready-gate input hydration（issue #759 P1）的端到端测试。

背景：本地 job_dir 是可淘汰缓存（EXEC-ARTIFACT-STORE-001），「清单行在、
本地文件没了」是正常态；而 ready 判定（find_ready_nodes / 分支条件）只
探测本地文件，manifest-only 输入会把 job 永久卡在 queued——
restore_missing_inputs 的唯一旧调用点在 claim 之后，到不了。修复在评估
miss 路径挂 hydration（``workflow_worker/input_hydration.py``），本文件
的用例都必须推进到 ready/claim，不只断言中间状态。
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.job_workflow_upgrade import seed_impl_identity
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code

_A_PAYLOAD = b'{"from": "a"}'


def _chain_definition(b_cap: str = "cap_b") -> WorkflowDefinition:
    """a → b 两级链：a 产出 a_out.json，b 声明它为输入。"""
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability=b_cap,
                after=["a"],
                config_schema={},
                inputs=["a_out.json"],
                outputs=["b_out.json"],
            ),
        },
    )


def _seed_manifest_row(queries: JobQueries, job_id: str, storage_key: str, payload: bytes) -> None:
    """落一条 (a, a_out.json) 清单行，内容与 FakeObjectStorage 中的对象一致。"""
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a', 'a_out.json', %s, %s, %s)
            """,
            (job_id, storage_key, len(payload), hashlib.sha256(payload).hexdigest()),
        )


def _reset_downstream(queries: JobQueries, job_id: str) -> None:
    """下游 b 被 rerun/reset 的最小真实路径（rerun 的原子 mutation）。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job_id, ["b"], {"b": []})


def _claimed_b(worker, queries: JobQueries, job_id: str) -> None:
    """b 已 claim（本地 code 池持租约 + future 已提交）。"""
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert len(worker.state.futures) == 1
    assert queries.get_job_node(job_id, "b")["status"] == "running"


def test_inherit_upgrade_manifest_only_artifact_reaches_claim(tmp_path: Path) -> None:
    """upgrade inherit 后被继承产物只剩清单行 → hydration 回填 → b ready → claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], _chain_definition())
    current = revisions.publish_workspace_revision(
        workspace["id"], _chain_definition(b_cap="cap_b_new")
    )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    # 播种真实 intake 会冻结的 frozen_config_json（legacy NULL-frozen 的旧侧
    # 配置基准不可证明，会保守退化为全量重跑）。
    from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
    from server.app.workflows.definition import workflow_definition_from_dict

    frozen = intake_frozen_config_json(
        queries,
        workspace["id"],
        workflow_definition_from_dict(json.loads(original["definition_json"])),
    )
    if frozen is not None:
        with closing(connect_database(queries.dsn_identity)) as conn, conn:
            conn.execute(
                "update jobs set frozen_config_json=%s where id=%s",
                (frozen, job["id"]),
            )
    # a 完成且实现身份可证明（published code + 完成执行同 hash）；b 旧 revision
    # 已完成（capability 变更后必重置，无需身份）。
    seed_impl_identity(queries, workspace, job["id"], ["a"])
    queries.update_job_node(job["id"], "b", status="completed")
    queries.update_job_status(job["id"], "completed")
    # a 的产物 manifest-only：本地文件从未落盘（等价于淘汰后形态），可达性
    # 预检靠清单行放行继承。
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    _seed_manifest_row(queries, job["id"], storage_key, _A_PAYLOAD)
    assert not (job_dir / "a_out.json").exists()

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert statuses == {"a": "completed", "b": "pending"}

    storage = FakeObjectStorage(objects={storage_key: _A_PAYLOAD})
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    # b 的 claim 需要新 revision 下 published node code（EXEC-CODE-002）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_chain_definition(b_cap="cap_b_new")],
        artifact_object_store=store,
    )
    worker._poll()

    # hydration 把 a_out.json 从对象存储回填到 job_dir，b 越过 ready gate 被 claim。
    assert (job_dir / "a_out.json").read_bytes() == _A_PAYLOAD
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]
    _claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_evicted_upstream_input_restored_after_downstream_rerun(tmp_path: Path) -> None:
    """本地淘汰场景：completed 上游产物本地被淘汰、下游 rerun → 下一轮评估回填后 ready。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = WorkflowDefinition(
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
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    queries.update_job_node(job["id"], "b", status="completed")
    queries.update_job_status(job["id"], "completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    (job_dir / "a_out.json").write_bytes(_A_PAYLOAD)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    _seed_manifest_row(queries, job["id"], storage_key, _A_PAYLOAD)

    # 维护线程淘汰本地产物（清单行已确认持久化），随后下游被 rerun。
    (job_dir / "a_out.json").unlink()
    _reset_downstream(queries, job["id"])

    storage = FakeObjectStorage(objects={storage_key: _A_PAYLOAD})
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    # b 的 claim 需要 published node code（EXEC-CODE-002）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )
    worker._poll()

    assert (job_dir / "a_out.json").read_bytes() == _A_PAYLOAD
    _claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_branch_condition_artifact_hydrated_before_branch_evaluation(tmp_path: Path) -> None:
    """分支条件引用的产物同样回填：hydration 先于 evaluate_branches 跑。

    不回填时 condition_matches 读不到本地文件 → 条件为假 → b 被错误标记
    not_applicable（永远不会 ready）。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["a_out.json"],
                outputs=["b_out.json"],
            ),
        },
        edges=[
            WorkflowEdge(
                source="a",
                target="b",
                condition=WorkflowCondition(artifact="a_out.json", path="$.from", equals="a"),
            )
        ],
    )
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    _seed_manifest_row(queries, job["id"], storage_key, _A_PAYLOAD)
    assert not (job_dir / "a_out.json").exists()

    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: _A_PAYLOAD})
    )
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )
    worker._poll()

    # 分支条件为真（回填后可读）→ b 未被标记 not_applicable，直接 ready → claim。
    assert queries.get_job_node(job["id"], "b")["status"] != "not_applicable"
    _claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_missing_object_defers_evaluation_without_caching(tmp_path: Path) -> None:
    """清单行有但对象缺失 → 节点保持 pending、不缓存评估结果（下轮重试）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = WorkflowDefinition(
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
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    _seed_manifest_row(queries, job["id"], storage_key, _A_PAYLOAD)
    # 对象存储里刻意没有该 key：open_stream 抛错 → 单文件恢复失败。

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    manifest_reads = 0
    original_rows_for_job = store.rows_for_job

    def counting_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        return original_rows_for_job(job_id)

    store.rows_for_job = counting_rows_for_job  # type: ignore[method-assign]
    # 播种 b 的 published code：排除「无 code」成为不 claim 的混杂因素。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )

    worker._poll()

    # 恢复失败：b 保持 pending（不置 failed）、无任何 claim、文件仍缺失。
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert not (job_dir / "a_out.json").exists()
    assert manifest_reads == 1
    # 关键纪律：不写入 job_evals 缓存，下一轮 poll 重新评估重试。
    assert job["id"] not in worker.state.job_evals

    worker._poll()

    assert manifest_reads == 2
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"

    worker.stop()


def test_no_object_storage_keeps_pre_hydration_behavior(tmp_path: Path) -> None:
    """未配置对象存储（store=None）→ hydration no-op，行为与现状一致。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = WorkflowDefinition(
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
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a", "b"],
        workspace_id=workspace["id"],
    )
    queries.update_job_node(job["id"], "a", status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    _seed_manifest_row(queries, job["id"], storage_key, _A_PAYLOAD)

    # 播种 b 的 published code：同上，唯一阻塞因素应只是缺失的输入。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, executor, [definition])
    worker._poll()

    # 输入本地缺失且无对象存储可回填：b 保持 pending；评估结果照常缓存
    # （与修复前一致——不ready 的判定本身被缓存，不做无谓重评）。
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert not (job_dir / "a_out.json").exists()
    assert job["id"] in worker.state.job_evals
    assert worker.state.job_evals[job["id"]][1] == []

    worker.stop()
