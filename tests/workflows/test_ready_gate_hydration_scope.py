"""Ready-gate hydration 的查询节奏与恢复面收窄（#759 复审 P1 族）回归。

姊妹文件 test_ready_gate_hydration.py 钉 hydration 的语义（恢复、defer、
代次夹逼）；本文件钉它的**代价与范围**：

- running job 每轮绕过评估缓存重评（scan.collect_ready_candidates），
  hydration 在没有任何可恢复清单行时不得做第二次代次读——恢复写为空、
  夹逼没有保护对象，否则扫描退化为每轮每 job 三次串行 DB 查询的 N+1
  （codex 复审 P1，eval_batch / input_hydration）。
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker, _seed_trivial_node_code


def _external_input_definition() -> WorkflowDefinition:
    """c（无依赖长跑）+ b（声明无生产者的外部输入 ext.json，永远等不到）。"""
    return WorkflowDefinition(
        key="wfext",
        label="Wf Ext",
        intake=WorkflowIntake(),
        nodes={
            "c": WorkflowNode(key="c", label="C", capability="cap_c", outputs=["c_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                inputs=["ext.json"],
                outputs=["b_out.json"],
            ),
        },
    )


def test_running_job_without_recoverable_rows_skips_generation_recheck(tmp_path: Path) -> None:
    """running job 每轮重评时 hydration 的 DB 查询定额：清单读 1 次 + 代次
    预读 1 次；无可恢复清单行（恢复写为空）时**不做**第二次代次读。

    修复前每轮每 job 串行 3 次查询（代次预读 + 清单 + 代次复核），running
    job 绕过评估缓存使该成本每轮重复——两个测量轮修复前代次读为 4 次。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfext", default_workflow_key="wfext", workspace_id="wfext"
    )
    job = queries.create_job(
        workflow_key="wfext",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["c", "b"],
        workspace_id=workspace["id"],
    )

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    manifest_reads = 0
    generation_reads = 0
    original_rows_for_job = store.rows_for_job

    def counting_rows_for_job(job_id: str):
        nonlocal manifest_reads
        manifest_reads += 1
        return original_rows_for_job(job_id)

    store.rows_for_job = counting_rows_for_job  # type: ignore[method-assign]
    # c 可 claim（长跑阻塞在 executor 上 → job 持 running 状态）。
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfext", "c")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_external_input_definition()],
        artifact_object_store=store,
    )
    original_get_generation = worker.job_db.get_job_execution_generation

    def counting_get_generation(job_id: str):
        nonlocal generation_reads
        generation_reads += 1
        return original_get_generation(job_id)

    worker.job_db.get_job_execution_generation = counting_get_generation  # type: ignore[method-assign]

    worker._poll()
    assert queries.get_job_node(job["id"], "c")["status"] == "running"
    worker._poll()  # claim bump 了 mark：本轮重评后 job 进入 running 缓存绕过态

    manifest_reads = 0
    generation_reads = 0
    worker._poll()
    worker._poll()

    # 两个测量轮：ext.json 本地缺失且无清单行（真缺失），恢复写恒为空——
    # 每轮清单读 1 次 + 代次预读 1 次，无第二次代次读。
    assert manifest_reads == 2
    assert generation_reads == 2

    executor.block_event.set()
    worker.stop()
