"""暂存闭包口径的 rerun/run-to 产物失效测试（#759 复审 P2 / codex #776 P1）。

自 ``test_job_rerun_manifest_gc.py`` 按主题拆出（零改动迁移）：

- 隐式消费者（无显式入边）并入同一重置闭包暂存/删行（重置集 ≡ 暂存集）；
- 同名纯输出生产者随重置闭包收敛（job_reset_closure）——闭包内翻
  pending、闭包外 stale 失效。
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revision_format import serialize_definition
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.fakes.storage import FakeObjectStorage

pytestmark = pytest.mark.postgres


def _seed_job_with_manifest(
    job_db: Any,
    settings: Any,
    definition: WorkflowDefinition,
    *,
    workspace: Any,
    storage: FakeObjectStorage,
    source_id: str = "Q1",
    node_outputs: tuple[tuple[str, str], ...] = (("up", "up.json"), ("down", "down.json")),
) -> dict[str, Any]:
    node_keys = [key for key, _ in node_outputs]
    batch = job_db.create_run(
        definition.key,
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id=source_id,
        run_id=batch["id"],
        title="Question 1",
        node_keys=node_keys,
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    for key in node_keys:
        job_db.update_job_node(job["id"], key, status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    store = JobArtifactObjectStore(job_db, storage)
    for node_key, name in node_outputs:
        (storage_dir / name).write_text(f"{name} content")
        store.upload(
            workspace_id=str(workspace["id"]),
            job_id=job["id"],
            node_key=node_key,
            name=name,
            local_path=storage_dir / name,
        )
    return job


def _make_rerun_service(job_db, settings, storage) -> JobRerunService:
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )


# ---------------------------------------------------------------------------
# #759 复审 P2：暂存面与重置面同一合并下游口径（隐式消费者）
# ---------------------------------------------------------------------------


@pytest.fixture
def implicit_consumer_definition():
    """down 隐式消费 up.json（声明 input 但无显式入边）。"""
    return WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "up": WorkflowNode(key="up", label="Up", capability="up", outputs=["up.json"]),
            "down": WorkflowNode(
                key="down",
                label="Down",
                capability="down",
                inputs=["up.json"],
                outputs=["down.json"],
            ),
        },
    )


def test_rerun_stages_implicit_consumer_outputs_and_rows(
    job_db, settings, implicit_consumer_definition
):
    """#759 复审 P2：隐式消费者的产物与清单行随同一闭包暂存/删除。

    down 是 up 的隐式消费者（无显式入边）：stale 面
    （``dependency_downstream``，显式边 ∪ 隐式消费边）已把 down 并入重
    跑，但暂存面此前按显式边 ``downstream_nodes`` 扩展——down 被标
    stale、``node_runs`` 引用清空，旧产物文件与清单行却存活：重跑未完成
    （重跑失败）的窗口里 artifact API 继续展示并回填旧字节（#508 语义对
    隐式消费者失效）。修复后暂存面与重置面同口径：down.json 本地暂存
    （提交后删除）、清单行事务内删除、对象 best-effort 清理。
    """
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, implicit_consumer_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "up")

    assert result["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert nodes == {"up": "pending", "down": "stale"}
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    assert not (storage_dir / "up.json").exists()
    assert not (storage_dir / "down.json").exists()
    # 重跑尚未完成的窗口内，down 的旧产物不再出 API 清单、也不再可回填。
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"up.json", "down.json"}


def test_run_to_stages_implicit_consumer_inside_target_closure(job_db, settings):
    """#759 复审 P2（run-to 入口）：目标闭包内的隐式消费者同口径暂存。

    mid 隐式消费 up.json（无显式入边）但经 mid→target 显式边落在
    target 的 ancestor closure 内。run_to(target, start=up) 的 stale 面按
    合并下游覆盖 mid/post：#759 codex P1（937b02744）起 closure 只界定
    run-to 的执行范围、不参与暂存判定——闭包外下游 post 的产物同样在
    重置集里，一并失效（否则 run-to 到达目标继续执行时 post 读到旧输入）。
    """
    definition = WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "up": WorkflowNode(key="up", label="Up", capability="up", outputs=["up.json"]),
            "mid": WorkflowNode(
                key="mid",
                label="Mid",
                capability="mid",
                inputs=["up.json"],
                outputs=["mid.json"],
            ),
            "target": WorkflowNode(
                key="target",
                label="Target",
                capability="target",
                after=["up", "mid"],
                inputs=["mid.json"],
                outputs=["t.json"],
            ),
            "post": WorkflowNode(
                key="post",
                label="Post",
                capability="post",
                after=["target"],
                outputs=["post.json"],
            ),
        },
    )
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db,
        settings,
        definition,
        workspace=workspace,
        storage=storage,
        node_outputs=(
            ("up", "up.json"),
            ("mid", "mid.json"),
            ("target", "t.json"),
            ("post", "post.json"),
        ),
    )
    store = JobArtifactObjectStore(job_db, storage)
    service = JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        object_store=store,
    )

    result = service.run_to(workspace["id"], job["id"], "target", start_node_key="up")

    assert result["status"] == "succeeded"
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    # 闭包内（含隐式消费者 mid）：产物暂存删除、清单行清除。
    assert not (storage_dir / "up.json").exists()
    assert not (storage_dir / "mid.json").exists()
    assert not (storage_dir / "t.json").exists()
    # 闭包外下游 post：自 #759 codex P1 起同样在重置集里（重置集≡暂存集，
    # closure 只界定执行范围），产物与清单行一并失效。
    assert not (storage_dir / "post.json").exists()
    assert store.names_for_job(job["id"]) == set()


# ---------------------------------------------------------------------------
# codex 复审 P1（#776）：同名纯输出的生产者必须随重置闭包一起重置
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_output_definition():
    """b/c 声明同名纯输出 x.json（对象键按名、不含 node 身份）。"""
    return WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "b": WorkflowNode(key="b", label="B", capability="b", outputs=["x.json"]),
            "c": WorkflowNode(key="c", label="C", capability="c", outputs=["x.json"]),
        },
    )


def test_rerun_resets_same_name_producer(job_db, settings, shared_output_definition):
    """codex 复审 P1：同名纯输出的生产者与重置节点一起重置（rerun 入口）。

    只重置 b 时 A3 同名排除让 x.json 既不暂存也不删行：b 的新 attempt
    若没写该文件，``_check_outputs`` 只查存在性，会把 c 遗留的旧字节
    当 b 的本次输出（静默串用）。与 upgrade 的同名生产者收敛（通道 B）
    同语义：c 一并 stale、x.json 暂存、两条清单行同事务删除。
    """
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db,
        settings,
        shared_output_definition,
        workspace=workspace,
        storage=storage,
        node_outputs=(("b", "x.json"), ("c", "x.json")),
    )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "b")

    assert result["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert nodes == {"b": "pending", "c": "stale"}
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    assert not (storage_dir / "x.json").exists()
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()


def _make_execution_service(job_db, settings, storage) -> JobExecutionService:
    return JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )


def test_run_to_resets_completed_same_name_producer_inside_closure(job_db, settings):
    """codex 复审 P1：run-to（无起始节点）闭包内 completed 同名生产者翻 pending。

    p1/p2 声明同名纯输出 s.json，target 消费之。p2 failed（入重置集）、
    p1 completed：p1 不重置则 p2 重跑不写真出文件时吃 p1 旧字节，或
    p2 写出后 p1 的清单行指向别人的内容。修复后 p1 一并翻 pending
    （p1 在目标闭包内、until_node 模式可执行），s.json 暂存 + 删行。
    """
    definition = WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "p1": WorkflowNode(key="p1", label="P1", capability="p1", outputs=["s.json"]),
            "p2": WorkflowNode(key="p2", label="P2", capability="p2", outputs=["s.json"]),
            "target": WorkflowNode(
                key="target",
                label="Target",
                capability="target",
                after=["p1", "p2"],
                inputs=["s.json"],
                outputs=["t.json"],
            ),
        },
    )
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db,
        settings,
        definition,
        workspace=workspace,
        storage=storage,
        node_outputs=(("p1", "s.json"), ("p2", "s.json"), ("target", "t.json")),
    )
    job_db.update_job_node(job["id"], "p2", status="failed")
    job_db.update_job_node(job["id"], "target", status="pending")
    service = _make_execution_service(job_db, settings, storage)

    result = service.run_to(workspace["id"], job["id"], "target")

    assert result["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert nodes == {"p1": "pending", "p2": "pending", "target": "pending"}
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    assert not (storage_dir / "s.json").exists()
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()


def test_run_to_stales_same_name_producer_outside_closure(job_db, settings):
    """codex 复审 P1：run-to 闭包外的同名生产者 stale 失效（本轮不执行）。

    mid failed 入重置集；c 与 mid 共享纯输出 shared.json（无任何消费者，
    合并上游也到不了 c——若共享的是 mid.json，c 会经隐式生产边落入目标
    闭包而翻 pending 本轮重跑）。c 在 until_node 模式下不可执行，按
    run-to-with-start 对闭包外下游的既有语义 stale 失效（下次 full run
    重跑），其产物与清单行一并失效——否则 mid 重跑不写文件时吃 c 的
    旧字节。up 不受影响。
    """
    definition = WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "up": WorkflowNode(key="up", label="Up", capability="up", outputs=["up.json"]),
            "mid": WorkflowNode(
                key="mid",
                label="Mid",
                capability="mid",
                after=["up"],
                inputs=["up.json"],
                outputs=["mid.json", "shared.json"],
            ),
            "target": WorkflowNode(
                key="target",
                label="Target",
                capability="target",
                after=["mid"],
                inputs=["mid.json"],
                outputs=["t.json"],
            ),
            "c": WorkflowNode(key="c", label="C", capability="c", outputs=["shared.json"]),
        },
    )
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db,
        settings,
        definition,
        workspace=workspace,
        storage=storage,
        node_outputs=(
            ("up", "up.json"),
            ("mid", "mid.json"),
            ("mid", "shared.json"),
            ("c", "shared.json"),
            ("target", "t.json"),
        ),
    )
    job_db.update_job_node(job["id"], "mid", status="failed")
    job_db.update_job_node(job["id"], "target", status="pending")
    service = _make_execution_service(job_db, settings, storage)

    result = service.run_to(workspace["id"], job["id"], "target")

    assert result["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert nodes == {"up": "completed", "mid": "pending", "target": "pending", "c": "stale"}
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    assert not (storage_dir / "mid.json").exists()
    assert not (storage_dir / "shared.json").exists()
    assert (storage_dir / "up.json").exists()
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == {"up.json"}
