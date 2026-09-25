"""Ready-gate input hydration（issue #759 P1）的端到端测试。

背景：本地 job_dir 是可淘汰缓存（EXEC-ARTIFACT-STORE-001），「清单行在、
本地文件没了」是正常态；而 ready 判定（find_ready_nodes / 分支条件）只
探测本地文件，manifest-only 输入会把 job 永久卡在 queued——
restore_missing_inputs 的唯一旧调用点在 claim 之后，到不了。修复在评估
miss 路径挂 hydration（``workflow_worker/input_hydration.py``），本文件
的用例都必须推进到 ready/claim，不只断言中间状态。upgrade/rerun 联动
场景见姊妹文件 ``test_ready_gate_hydration_upgrade.py``（codex #776 R8
拆分）。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.ready_gate_hydration import (
    A_PAYLOAD,
    assert_claimed_b,
    chain_definition,
    pending_b_job,
    seed_manifest_row,
)
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code


def test_branch_condition_artifact_hydrated_before_branch_evaluation(tmp_path: Path) -> None:
    """分支条件引用的产物同样回填：hydration 先于 evaluate_branches 跑。

    b 的唯一依赖渠道是边条件（无 inputs 声明——#775 对抗复审 P2：带
    inputs 时本用例在 condition 渠道完全失效的突变下也绿，是假绿）。
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
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
    assert not (job_dir / "a_out.json").exists()

    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    )
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )
    worker._poll()

    # 分支条件为真（回填后可读）→ b 未被标记 not_applicable，直接 ready → claim。
    assert queries.get_job_node(job["id"], "b")["status"] != "not_applicable"
    assert_claimed_b(worker, queries, job["id"])

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
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
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


def test_generation_bump_during_hydration_discards_restored_files(tmp_path: Path) -> None:
    """代次复核（#702 P1）：恢复写与复核之间 mutation 提交 → 恢复文件被删除。

    交错构造：包装 ``rows_for_job``，在 hydration 读到清单行之后、恢复写
    与第二次代次读取之前，模拟 mutation 提交的两件事（删清单行 + bump
    ``jobs.execution_generation``，与 mark_nodes_for_rerun 同事务）。断言：
    本轮恢复落盘的文件被删除、不写评估缓存、节点保持 pending、下一轮
    不再回填（清单行已删）。
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
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)
    assert not (job_dir / "a_out.json").exists()

    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    )
    original_rows_for_job = store.rows_for_job
    mutation_committed = False

    def mutating_rows_for_job(job_id: str):
        rows = original_rows_for_job(job_id)
        nonlocal mutation_committed
        if not mutation_committed:
            mutation_committed = True
            # 模拟 reset mutation 在 hydration 的两次代次读取之间提交：
            # 清单行删除与代次 bump 在真实路径上是同一事务（EXEC-GENERATION-001）。
            with closing(connect_database(queries.dsn_identity)) as conn, conn:
                conn.execute(
                    "delete from job_artifacts where job_id=%s and name='a_out.json'", (job_id,)
                )
                conn.execute(
                    "update jobs set execution_generation=execution_generation+1 where id=%s",
                    (job_id,),
                )
        return rows

    store.rows_for_job = mutating_rows_for_job  # type: ignore[method-assign]
    # 播种 b 的 published code：排除「无 code」成为不 claim 的混杂因素。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "test", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [definition], artifact_object_store=store
    )

    worker._poll()

    # 代次失配：恢复的字节被丢弃（文件不存在）、b 保持 pending、无 claim、
    # 不写 job_evals 缓存（恢复不全按不缓存纪律处理）。
    assert mutation_committed
    assert not (job_dir / "a_out.json").exists()
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert job["id"] not in worker.state.job_evals

    worker._poll()

    # 清单行已删：下一轮不再回填；本轮评估为不 ready 并正常缓存。
    assert not (job_dir / "a_out.json").exists()
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.state.job_evals[job["id"]][1] == []

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
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)

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


def test_manifest_read_failure_defers_without_caching_and_recovers(tmp_path: Path) -> None:
    """清单读抛错 → 不缓存、不产候选、下轮重试；故障清除后重新读清单并 claim。

    对象存储清单是产物权威副本（EXEC-ARTIFACT-STORE-001）：读失败时本地缺失
    不得被缓存为真缺失——否则 mark 不再变化、故障恢复后清单永不再读，job
    永久停 queued（parked-forever 回归）。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    job = pending_b_job(queries, workspace)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)

    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    )
    manifest_reads = 0
    original_rows_for_job = store.rows_for_job
    failing = True

    def flaky_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        if failing:
            raise RuntimeError("manifest read boom")
        return original_rows_for_job(job_id)

    store.rows_for_job = flaky_rows_for_job  # type: ignore[method-assign]
    # 播种 b 的 published code：排除「无 code」成为不 claim 的混杂因素。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [chain_definition()], artifact_object_store=store
    )

    worker._poll()

    # 读失败按「恢复不全」同款处理：不缓存、不产候选、节点保持 pending。
    assert manifest_reads == 1
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert not (job_dir / "a_out.json").exists()
    assert job["id"] not in worker.state.job_evals

    worker._poll()

    assert manifest_reads == 2  # 未缓存 → 下轮重试清单读

    failing = False
    worker._poll()

    # 故障恢复后同轮重新读清单、回填、越过 ready gate 被 claim（不停 queued）。
    assert (job_dir / "a_out.json").read_bytes() == A_PAYLOAD
    assert_claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_generation_preread_failure_defers_without_caching(tmp_path: Path) -> None:
    """代次预读失败 → 不读清单、不缓存、下轮重试；读恢复后正常 hydration 并 claim。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    job = pending_b_job(queries, workspace)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    storage_key = f"jobs/{workspace['id']}/{job['id']}/a_out.json"
    seed_manifest_row(queries, job["id"], storage_key, A_PAYLOAD)

    store = JobArtifactObjectStore(
        TEST_DATABASE_URL, FakeObjectStorage(objects={storage_key: A_PAYLOAD})
    )
    manifest_reads = 0
    original_rows_for_job = store.rows_for_job

    def counting_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        return original_rows_for_job(job_id)

    store.rows_for_job = counting_rows_for_job  # type: ignore[method-assign]
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [chain_definition()], artifact_object_store=store
    )
    original_get_generation = worker.job_db.get_job_execution_generation

    def raising_get_generation(job_id: str):
        raise RuntimeError("generation read boom")

    worker.job_db.get_job_execution_generation = raising_get_generation  # type: ignore[method-assign]

    worker._poll()

    # 预读失败在清单读之前短路：不读清单、不缓存、节点保持 pending。
    assert manifest_reads == 0
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert job["id"] not in worker.state.job_evals

    worker.job_db.get_job_execution_generation = original_get_generation  # type: ignore[method-assign]
    worker._poll()

    # 读恢复后重新评估：清单被读取、输入回填、b 被 claim。
    assert manifest_reads == 1
    assert (job_dir / "a_out.json").read_bytes() == A_PAYLOAD
    assert_claimed_b(worker, queries, job["id"])

    executor.block_event.set()
    worker.stop()


def test_successful_manifest_read_without_rows_caches_evaluation(tmp_path: Path) -> None:
    """清单读成功但该名字无清单行 → 真缺失：正常评估为 not-ready 并缓存。

    防过修：defer 只覆盖「读失败/恢复不全」，「读成功且无行」必须沿用可缓存
    的正常评估（该降级重跑的场景已在 upgrade plan 拦过），否则每个真缺失输入
    的 job 每轮都做无谓的清单重读。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    job = pending_b_job(queries, workspace)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    # 本地无 a_out.json，也刻意不播种清单行：输入真缺失。

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    manifest_reads = 0
    original_rows_for_job = store.rows_for_job

    def counting_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        return original_rows_for_job(job_id)

    store.rows_for_job = counting_rows_for_job  # type: ignore[method-assign]
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [chain_definition()], artifact_object_store=store
    )

    worker._poll()

    assert manifest_reads == 1
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"
    assert not (job_dir / "a_out.json").exists()
    assert job["id"] in worker.state.job_evals
    assert worker.state.job_evals[job["id"]][1] == []

    worker._poll()

    # 缓存命中：不再重读清单、不重评。
    assert manifest_reads == 1
    assert queries.get_job_node(job["id"], "b")["status"] == "pending"

    worker.stop()
