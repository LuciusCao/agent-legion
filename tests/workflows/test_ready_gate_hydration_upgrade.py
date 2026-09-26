"""Ready-gate hydration 的 upgrade/rerun 联动场景（#759 P1；codex #776 R8 拆分）。

自 ``test_ready_gate_hydration.py`` 按主题拆出（用例零改动迁移）：inherit
升级后 manifest-only 继承产物的回填到 claim、下游 rerun 后被淘汰上游输入
的恢复、rerun 条件生产者的分支推迟。主流 hydration 行为（miss 推迟 /
代次交错 / 缓存纪律）见原文件。
"""

from __future__ import annotations

import hashlib
import json
import time
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
from tests.helpers.ready_gate_hydration import (
    A_PAYLOAD,
    assert_claimed_b,
    chain_definition,
    reset_downstream,
    seed_manifest_row,
)
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code


def test_inherit_upgrade_manifest_only_artifact_reaches_claim(tmp_path: Path) -> None:
    """upgrade inherit 后被继承产物只剩清单行 → hydration 回填 → b ready → claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], chain_definition())
    current = revisions.publish_workspace_revision(
        workspace["id"], chain_definition(b_cap="cap_b_new")
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
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
    assert not (job_dir / "a_out.json").exists()

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert statuses == {"a": "completed", "b": "pending"}

    storage = FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    # b 的 claim 需要新 revision 下 published node code（EXEC-CODE-002）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [chain_definition(b_cap="cap_b_new")],
        artifact_object_store=store,
    )
    worker._poll()

    # hydration 把 a_out.json 从对象存储回填到 job_dir，b 越过 ready gate 被 claim。
    assert (job_dir / "a_out.json").read_bytes() == A_PAYLOAD
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]
    assert_claimed_b(worker, queries, job["id"])

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
    (job_dir / "a_out.json").write_bytes(A_PAYLOAD)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)

    # 维护线程淘汰本地产物（清单行已确认持久化），随后下游被 rerun。
    (job_dir / "a_out.json").unlink()
    reset_downstream(queries, job["id"])

    storage = FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    # b 的 claim 需要 published node code（EXEC-CODE-002）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )
    worker._poll()

    assert (job_dir / "a_out.json").read_bytes() == A_PAYLOAD
    assert_claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_rerun_condition_producer_defers_branch_until_producer_completes(
    tmp_path: Path,
) -> None:
    """#759 ③ 对抗复审 P1 端到端：重跑条件产物生产者（与分支源不相邻）时，
    gated 分支的裁决推迟到生产者完成——缺失的条件文件不被当成 false（不
    标 not_applicable 终态），生产者完成后按新字节正常选中、claim。

    修复前：rerun 的暂存删掉 verdict.json 后，下一轮评估条件为假 → gated
    被永久标 not_applicable（job 以「分支被跳过」静默完成）。"""
    from server.app.workflows.workflow_consumption import dependency_downstream

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("test", default_workflow_key="test", workspace_id="test")
    definition = WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "entry": WorkflowNode(key="entry", label="E", capability="cap_e"),
            "scorer": WorkflowNode(
                key="scorer",
                label="S",
                capability="cap_s",
                after=["entry"],
                outputs=["verdict.json"],
            ),
            "gated": WorkflowNode(
                key="gated",
                label="G",
                capability="cap_g",
                after=["entry"],
                outputs=["g.json"],
            ),
        },
        edges=[
            WorkflowEdge(source="entry", target="scorer"),
            WorkflowEdge(
                source="entry",
                target="gated",
                condition=WorkflowCondition(artifact="verdict.json", path="$.done", equals=True),
            ),
        ],
    )
    job = queries.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["entry", "scorer", "gated"],
        workspace_id=workspace["id"],
    )
    for key in ("entry", "scorer", "gated"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    verdict = b'{"done": true}'
    (job_dir / "verdict.json").write_bytes(verdict)
    (job_dir / "g.json").write_bytes(verdict)
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'scorer', 'verdict.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/test/{job['id']}/verdict.json",
                len(verdict),
                hashlib.sha256(verdict).hexdigest(),
            ),
        )

    # rerun scorer（真实闭包 + 原子突变）：gated 经条件消费边进 stale，
    # verdict.json 暂存删除（本地文件随暂存消失、清单行删除）。
    downstream = dependency_downstream(definition, "scorer")
    assert "gated" in downstream  # 条件消费边进闭包（③ 层的前提）
    (job_dir / "verdict.json").unlink()  # 暂存的本地面效果
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(
            conn,
            job["id"],
            ["scorer"],
            {"scorer": downstream},
            staged_artifact_names=frozenset({"verdict.json"}),
        )

    for key in ("scorer", "gated"):
        _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", key)
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=None
    )

    worker._poll()

    # 屏障生效：gated 不被标 not_applicable（stale 等待生产者）；scorer 已
    # 被 claim（重跑在途）。
    assert queries.get_job_node(job["id"], "gated")["status"] == "stale"
    assert queries.get_job_node(job["id"], "scorer")["status"] == "running"

    executor.block_event.set()  # scorer 重跑完成：写回 verdict.json
    # 完成回收发生在下一轮 poll 开头（reap_futures）——xdist 负载下执行器线
    # 程可能赶不上紧随的一轮，轮询到有界上限（等不到即红）。
    deadline = time.monotonic() + 15
    while queries.get_job_node(job["id"], "gated")["status"] != "running":
        assert time.monotonic() < deadline, "gated was never claimed after the producer re-ran"
        worker._poll()

    # 生产者完成后按新字节裁决：gated 被选中并 claim（永不曾 not_applicable）。
    assert queries.get_job_node(job["id"], "scorer")["status"] == "completed"
    worker.stop()
