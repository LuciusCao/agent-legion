"""Ready-gate hydration 的查询节奏与恢复面收窄（#759 复审 P1 族）回归。

姊妹文件 test_ready_gate_hydration.py 钉 hydration 的语义（恢复、defer、
代次夹逼）；本文件钉它的**代价与范围**：

- running job 每轮绕过评估缓存重评（scan.collect_ready_candidates），
  hydration 在没有任何可恢复清单行时不得做第二次代次读——恢复写为空、
  夹逼没有保护对象，否则扫描退化为每轮每 job 三次串行 DB 查询的 N+1
  （codex 复审 P1，eval_batch / input_hydration）。
- 恢复/defer 面按当前 node statuses 收窄：已完成并被淘汰缓存的 job 做
  单分支 targeted rerun 时，其他终态分支永久丢失/损坏的对象不得挟持
  整个 job 的评估（codex 复审 P1，input_hydration 的恢复面从全定义消
  费索引键集收窄为本轮探针集）。
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import mark_nodes_for_rerun
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


def _two_branch_definition() -> WorkflowDefinition:
    """a1 → a2（a2 消费 a1_out.json）与独立分支 b：两条互不相干的支路。"""
    return WorkflowDefinition(
        key="wf2br",
        label="Wf Two Branch",
        intake=WorkflowIntake(),
        nodes={
            "a1": WorkflowNode(key="a1", label="A1", capability="cap_a1", outputs=["a1_out.json"]),
            "a2": WorkflowNode(
                key="a2",
                label="A2",
                capability="cap_a2",
                after=["a1"],
                inputs=["a1_out.json"],
                outputs=["a2_out.json"],
            ),
            "b": WorkflowNode(key="b", label="B", capability="cap_b", outputs=["b_out.json"]),
        },
    )


def test_terminal_branch_lost_object_does_not_block_targeted_rerun(tmp_path: Path) -> None:
    """已完成并被淘汰缓存的 job 单分支 targeted rerun：另一终态分支的
    manifest-only 产物对象永久丢失也不得挟持本轮评估。

    修复前恢复面是整个定义的消费索引键集：a1_out.json（消费者 a2 已
    completed）恢复失败 → 非空 unrestored 跳过整个 job 的评估缓存，b 的
    rerun 永远到不了 claim。收窄后 a1_out.json 不在本轮探针集（其唯一消
    费者终态），b 照常评估并被 claim。
    """
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wf2br", default_workflow_key="wf2br", workspace_id="wf2br"
    )
    job = queries.create_job(
        workflow_key="wf2br",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["a1", "a2", "b"],
        workspace_id=workspace["id"],
    )
    for key in ("a1", "a2", "b"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    # 终态分支的清单行在、本地文件被淘汰、对象永久丢失（FakeObjectStorage
    # 刻意为空）——该对象本轮没有任何可运行/可评估的消费者。
    payload = b'{"from": "a1"}'
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a1', 'a1_out.json', %s, %s, %s)
            """,
            (
                job["id"],
                f"jobs/{workspace['id']}/{job['id']}/a1_out.json",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    # targeted rerun b（真实原子 mutation：b 回 pending、bump 代次）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        mark_nodes_for_rerun(conn, job["id"], ["b"], {"b": []})

    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wf2br", "b")
    executor = RecordingExecutor("code")
    worker = _make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [_two_branch_definition()],
        artifact_object_store=store,
    )

    worker._poll()

    # 无关终态分支的丢失对象不进入 defer 集：b 的 rerun 越过评估直接被
    # claim（本地 code 池持租约 + future 已提交）。
    assert queries.get_job_node(job["id"], "b")["status"] == "running"
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert queries.get_job_node(job["id"], "a2")["status"] == "completed"  # 终态分支原样

    executor.block_event.set()
    worker.stop()
