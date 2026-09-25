"""issue #759 复审 P1-A 反例的端到端回归：upgrade 后 ready/claim/执行结果。

场景（codex P1-A 最小反例）：``p(outputs=["x.json"])`` 纯产、
``c(inputs=["x.json"])`` 纯消费、两者间**无显式边**（纯文件名关联），
升级把两者都 reset。旧缺陷：clean/全退化分支暂存了本地 x 字节却保留旧
清单行（``unprotected_input_names`` 判 x 时禁用 x 自己的隐式消费边，永远
证不出可清理）——ready 前 hydration 立刻从权威对象复活旧字节，c 在 p
重跑前消费旧 revision 产物。

本文件的用例必须推进到 ready/claim/**执行结果**（与
``test_ready_gate_hydration.py`` 同纪律），并在升级后与执行结束后各做一
次三面断言（本地文件 / ``job_artifacts`` 清单行 / 权威对象字节）：

- 升级后：旧 x 三面失效（文件暂存删除、清单行删除、对象删除）；
- 执行面：c 在 p 产生新 x 之前不得 ready/claim；c 最终读到的必须是 p 重跑
  写出的新字节；job 完成。
"""

from __future__ import annotations

import hashlib
import threading
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import _make_worker, _seed_trivial_node_code

_OLD_X = b"old-x"
_NEW_X = b"new:x.json"


def _node(key: str, capability: str, **kwargs) -> WorkflowNode:
    return WorkflowNode(key=key, label=key.upper(), capability=capability, **kwargs)


def _counterexample(cap_p: str, cap_c: str) -> WorkflowDefinition:
    """p 纯产 x、c 纯消费 x、无显式边（纯文件名关联）。"""
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "p": _node("p", cap_p, outputs=["x.json"]),
            "c": _node("c", cap_c, inputs=["x.json"]),
        },
    )


class _BlockingIoExecutor:
    """记录每个节点执行时读到的输入字节，并写出新产物字节。

    首个被执行的节点（p）在 ``gate`` 放行前保持 running——测试借此在
    「p 尚未写出新 x」的窗口断言 c 未 ready/未 claim。
    """

    kind = "code"

    def __init__(self, gate: threading.Event) -> None:
        self.id = "code"
        self.gate = gate
        self.executed: list[str] = []
        self.inputs_seen: dict[str, dict[str, bytes]] = {}

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        node_key = str(context.node_key)
        # 先记录到达再阻塞：claim 后 executor 线程何时被调度不可控，
        # 测试的中态断言只看 DB 侧 running 与 futures（claim 同步落库）。
        self.executed.append(node_key)
        assert self.gate.wait(timeout=10), "executor was not released in time"
        self.inputs_seen[node_key] = {
            name: (context.job_dir / name).read_bytes() for name in context.inputs
        }
        for output in context.expected_outputs:
            (context.job_dir / output).write_bytes(f"new:{output}".encode())
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        del execution_id


def _seed_old_x(queries: JobQueries, job_id: str, storage: FakeObjectStorage) -> str:
    """旧 x 的三面现场：本地文件 + (p, x.json) 清单行 + 权威对象字节。"""
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_bytes(_OLD_X)
    storage_key = f"jobs/wfchain/{job_id}/x.json"
    storage.objects[storage_key] = _OLD_X
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'p', 'x.json', %s, %s, %s)
            """,
            (job_id, storage_key, len(_OLD_X), hashlib.sha256(_OLD_X).hexdigest()),
        )
    return storage_key


def _manifest_rows(queries: JobQueries, job_id: str) -> set[tuple[str, str]]:
    return queries.job_artifact_manifest_names_for_nodes(job_id, {"p", "c"})


def _run_to_completion(worker, queries: JobQueries, job_id: str) -> None:
    import time

    for _ in range(50):
        worker._poll()
        job = queries.get_job(job_id)
        if job and job["status"] == "completed":
            break
        time.sleep(0.05)


def _test_upgrade_then_execute_fresh_bytes(tmp_path: Path, *, mode: str) -> None:
    """反例全生命周期：upgrade（clean 或 inherit 全退化）→ ready/claim/执行。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "wfchain", default_workflow_key="wfchain", workspace_id="wfchain"
    )
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(
        workspace["id"], _counterexample("cap_p", "cap_c")
    )
    new_definition = _counterexample("cap_p_new", "cap_c_new")
    revisions.publish_workspace_revision(workspace["id"], new_definition)
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["p", "c"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    job_id = str(job["id"])
    for key in ("p", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    if mode == "inherit":
        # 旧快照不可解析 ⇒ inherit 保守退化为全量重跑（全退化分支，P2-D）。
        with closing(connect_database(queries.dsn_identity)) as conn, conn:
            conn.execute(
                "update jobs set workflow_definition_snapshot_json='{bad json' where id=%s",
                (job_id,),
            )
    storage = FakeObjectStorage()
    x_key = _seed_old_x(queries, job_id, storage)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)

    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        object_store=store,
    )
    result = service.upgrade(workspace["id"], job_id, mode=mode)

    # 升级后三面失效：本地文件、清单行、权威对象。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert not (job_dir / "x.json").exists()
    assert _manifest_rows(queries, job_id) == set()
    assert x_key in storage.deleted or x_key not in storage.objects

    # 执行面：p/c 的 code 均已发布（EXEC-CODE-002），worker 用新 definition。
    for node_key in ("p", "c"):
        _seed_trivial_node_code(TEST_DATABASE_URL, workspace["id"], "wfchain", node_key)
    gate = threading.Event()
    executor = _BlockingIoExecutor(gate)
    worker = _make_worker(
        tmp_path, TEST_DATABASE_URL, executor, [new_definition], artifact_object_store=store
    )

    worker._poll()

    # p claimed（本地 code 池持租约 + future 已提交）且阻塞在执行中
    # （尚未写出新 x）：x 三面缺席 ⇒ c 不得 ready、不得被 claim。
    assert len(worker.state.futures) == 1
    assert queries.get_job_node(job_id, "p")["status"] == "running"
    assert queries.get_job_node(job_id, "c")["status"] == "pending"
    assert not (job_dir / "x.json").exists()

    gate.set()
    _run_to_completion(worker, queries, job_id)
    worker.stop()

    # c 在 p 之后执行，且读到的是 p 重跑写出的新字节（旧字节三面失效后
    # hydration 无从复活）；job 完成。
    assert executor.executed == ["p", "c"]
    assert executor.inputs_seen["c"] == {"x.json": _NEW_X}
    assert queries.get_job(job_id)["status"] == "completed"
    # 执行后三面终态：本地文件是新字节；清单行/对象未被旧字节复活
    # （RecordingExecutor 形态的 executor 不做镜像上传，行/对象保持缺席）。
    assert (job_dir / "x.json").read_bytes() == _NEW_X
    assert _manifest_rows(queries, job_id) == set()
    assert x_key not in storage.objects


def test_clean_upgrade_implicit_consumer_waits_for_fresh_bytes(tmp_path: Path) -> None:
    """验收 1/6（clean 分支）。"""
    _test_upgrade_then_execute_fresh_bytes(tmp_path, mode="clean")


def test_degenerate_inherit_implicit_consumer_waits_for_fresh_bytes(tmp_path: Path) -> None:
    """验收 2/6（inherit 全退化分支：旧快照损坏 ⇒ 全量重跑）。"""
    _test_upgrade_then_execute_fresh_bytes(tmp_path, mode="inherit")
