"""issue #759 阶段 4 边界项的升级测试。

- 4.1 RMW startup 名保护收紧：同名 pure producer 排在 consumer 之后不
  构成「保证先行」，旧 manifest/对象保留（反例）；producer 保证先于
  全部 consumer（依赖邻接可达，同名隐式消费边不算证据）才放行清理。
- 4.2 旧 RMW output 退役：被删除/改写节点的旧 RMW 名在新图完全不再
  被引用时三面清理（清单行 + 本地文件 + 提交后对象删除编排）。
- 4.3 被删节点身份来自 old/new definition 差集：job_nodes 行缺失的
  漂移场景下产物面仍按差集清理。
- 4.4 guard 事务内重读 active revision（TOCTOU）：不符 → 整体重试
  一次成功；二次仍不符 → 冲突结果（skipped/revision_changed）且零
  副作用。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers.job_workflow_upgrade import (
    publish_node_code as _publish_node_code,
)
from tests.helpers.job_workflow_upgrade import (
    seed_done_execution as _seed_done_execution,
)
from tests.postgres_support import TEST_DATABASE_URL


def _node(
    key: str,
    capability: str | None = None,
    *,
    after: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key.upper(),
        capability=capability or f"cap_{key}",
        after=after or [],
        inputs=inputs or [],
        outputs=outputs or [],
    )


def _definition(nodes: dict[str, WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(key="wfchain", label="Wf Chain", intake=WorkflowIntake(), nodes=nodes)


def _setup(tmp_path: Path, definition: WorkflowDefinition):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    return queries, workspace, revisions, original


def _seed_job(queries, workspace, original, node_keys) -> str:
    job = queries.create_job(
        workflow_key="wfchain",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Question 1",
        node_keys=node_keys,
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    return str(job["id"])


def _seed_frozen(queries: JobQueries, job_id: str, node_keys: list[str]) -> None:
    """按 intake 默认段形状播种 frozen（参照 codex4 CRITICAL-1 的播种形态）。"""
    section = {"sandbox_network": False, "timeout_seconds": 600}
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({key: dict(section) for key in node_keys}), job_id),
        )


def _make_service(
    tmp_path: Path, queries: JobQueries, *, object_store: Any = None
) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        object_store=object_store,
    )


def _job_dir(queries: JobQueries, job_id: str) -> Path:
    from server.app.storage_paths import resolve_job_dir

    return resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)


def _insert_manifest_row(queries: JobQueries, job_id: str, node_key: str, name: str) -> str:
    storage_key = f"jobs/wschain/{job_id}/{name}"
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, %s, %s, %s, 1, 'hash')
            """,
            (job_id, node_key, name, storage_key),
        )
    return storage_key


def _manifest_names(queries: JobQueries, job_id: str, node_keys: set[str]) -> set[tuple[str, str]]:
    return queries.job_artifact_manifest_names_for_nodes(job_id, node_keys)


def _seed_kept_a(queries: JobQueries, workspace: dict, job_id: str) -> None:
    """给 a 播种可证明的实现身份（P1-1 基准），使其成为继承保留节点。"""
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    queries.update_job_node(job_id, "a", status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)


# ---------------------------------------------------------------------------
# 4.1 RMW startup 名的保护收紧（反例 + 放行）
# ---------------------------------------------------------------------------


def _rmw_late_producer_definitions() -> tuple[WorkflowDefinition, WorkflowDefinition]:
    """旧图 a→d→q（d 纯产 x）；新图删 d、q 变 RMW、p 纯产 x 且排在 q 之后。"""
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "d": _node("d", after=["a"], outputs=["x.json"]),
            "q": _node("q", after=["d"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "q": _node("q", "cap_q_new", after=["a"], inputs=["x.json"], outputs=["x.json"]),
            # p 纯产 x 但显式排在 q 之后——不能保证先于 q 执行。
            "p": _node("p", after=["q"], outputs=["x.json"]),
        }
    )
    return old, new


def test_rmw_startup_name_protected_when_pure_producer_runs_after_consumer(
    tmp_path: Path,
) -> None:
    """4.1 反例：q 是 x 的 RMW 节点，p 纯产 x 但 q→p（p 排在 q 之后）。

    旧缺陷：保护判定 = inputs − 纯输出——图中存在任意同名 pure
    producer（p）即判 x 不受保护，旧 x 的清单行/本地文件进清理面；但 p
    排在 q 之后，q 的首跑失去启动输入（restore/hydration 也无清单可回）。
    现行语义（#759 复审 P1-A，``input_protection_plan``）：x 是 RMW 附着名
    （旧文件不进暂存面而存活），其唯一 consumer q 没有对 p 的排序证据
    （RMW 名的隐式边不构成因果序）⇒ keep——保留旧 x 作 q 的启动输入。
    """
    old, new = _rmw_late_producer_definitions()
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "d", "q"])
    _seed_kept_a(queries, workspace, job_id)
    for key in ("d", "q"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "d", "q"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("startup-x")
    _insert_manifest_row(queries, job_id, "d", "x.json")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a 继承；q 变更重置、p 新增。x 的保护必须保留：旧 x 是 q 首跑的启动
    # 输入（p 保证不了先行），清单行与本地文件都不进清理面。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "q": "pending", "p": "pending"}
    assert (job_dir / "x.json").read_text() == "startup-x"
    assert ("d", "x.json") in _manifest_names(queries, job_id, {"d"})


def test_rmw_startup_name_unprotected_when_producer_guaranteed_before_consumers(
    tmp_path: Path,
) -> None:
    """4.1 放行：p 纯产 x 且经显式边保证先于唯一 consumer（q RMW）。

    p→q 显式边使 q 有对 p 的排序证据——p 重跑重新产出 x 后 q 才解锁，
    旧 x 的清单行与本地文件可安全清理。证明判别点是「保证先行」而非
    「存在同名 producer 即一律保护」的粗面。
    """
    old, _ = _rmw_late_producer_definitions()
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            # p 排在 q 之前（p→q）：q 保证在 x 的 producer 之后执行。
            "p": _node("p", after=["a"], outputs=["x.json"]),
            "q": _node("q", "cap_q_new", after=["p"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "d", "q"])
    _seed_kept_a(queries, workspace, job_id)
    for key in ("d", "q"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "d", "q"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("startup-x")
    _insert_manifest_row(queries, job_id, "d", "x.json")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "p": "pending", "q": "pending"}
    # x 失去保护：旧文件与旧清单行清理（p 会在 q 之前重新产出 x）。
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"d", "q"}) == set()


def test_clean_mode_keeps_rmw_startup_row_when_producer_runs_after_consumer(
    tmp_path: Path,
) -> None:
    """4.1 clean 路径：全量清单清理的 keep 集同样按「保证先行」收紧。

    clean 模式下 RMW 名不进暂存面（#114），唯一防线是
    ``delete_all_artifact_rows`` 的 keep_input_names：同名 pure producer
    排在 consumer 之后时 x 必须留在保护集，否则清单行被删、q 首跑的
    启动输入失去 hydration 回填来源。
    """
    old = _definition(
        {
            "a": _node("a"),
            "q": _node("q", after=["a"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "a": _node("a"),
            "q": _node("q", after=["a"], inputs=["x.json"], outputs=["x.json"]),
            # p 纯产 x 但排在 q 之后（q→p）。
            "p": _node("p", after=["q"], outputs=["x.json"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "q"])
    for key in ("a", "q"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_text("startup-x")
    _insert_manifest_row(queries, job_id, "q", "x.json")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="clean")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["status"] == "succeeded"
    assert set(statuses.values()) == {"pending"}
    # x 的清单行保留（保护收紧）；本地文件本就不进 clean 的暂存面（RMW）。
    assert ("q", "x.json") in _manifest_names(queries, job_id, {"q"})
    assert (job_dir / "x.json").read_text() == "startup-x"


# ---------------------------------------------------------------------------
# 4.2 旧 RMW output 的退役清理（新图完全不再引用）
# ---------------------------------------------------------------------------


class _RecordingObjectStore:
    """对象存储桩：记录 delete_objects 收到的 storage_key。"""

    enabled = True

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def live_keys_for(self, job_id: str, keys: list[str]) -> set[str]:
        return set()

    def delete_objects(self, rows: list[dict]) -> None:
        self.deleted.extend(str(row["storage_key"]) for row in rows)


def test_retired_rmw_output_of_rewritten_node_cleaned_three_faces(tmp_path: Path) -> None:
    """4.2 改写面：b 从 RMW(x) 改写为纯产 y，新图不再引用 x → 三面清理。

    旧缺陷：``removed_artifact_face`` 的重置节点分支用纯输出
    （outputs − inputs）做差——RMW 名 x 永远进不了候选面，清单行、本地
    文件与权威对象永久残留（artifact API 继续展示新图已不存在的产物）。
    修复：RMW 名同样进候选，仍被新图引用的名由保护集（4.1）/keep_io
    兜底，完全不再引用的名退役。
    """
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", after=["a"], inputs=["x.json"], outputs=["x.json"]),
            "c": _node("c", after=["b"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", "cap_b_new", after=["a"], outputs=["y.json"]),
            "c": _node("c", after=["b"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    _seed_kept_a(queries, workspace, job_id)
    for key in ("b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "b", "c"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("stale-rmw")
    x_key = _insert_manifest_row(queries, job_id, "b", "x.json")
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    # 三面清理：本地文件、清单行、提交后对象删除编排（storage_key 送达）。
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"b"}) == set()
    assert x_key in store.deleted


def test_retired_rmw_output_of_deleted_node_cleaned_with_runs_dir(tmp_path: Path) -> None:
    """4.2 删除面：被删节点 d 的 RMW 名在新图无任何引用 → 名 + runs 目录清理。

    旧缺陷：被删节点分支同样只收纯输出——RMW 名的文件与清单行残留
    （runs 目录清理此前已覆盖，这里钉住名字面）。
    """
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", after=["a"], outputs=["b_out.json"]),
            "c": _node("c", after=["b"]),
            "d": _node("d", after=["c"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", after=["a"], outputs=["b_out.json"]),
            "c": _node("c", after=["b"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c", "d"])
    # a/b/c 的实现身份均可证明（删除面独立于重置面，CRITICAL-1 形态）。
    for key in ("a", "b"):
        impl_hash = _publish_node_code(
            queries, workspace["id"], key, f"def run(ctx):\n    return {{{key!r}}}\n"
        )
        queries.update_job_node(job_id, key, status="pending")
        _seed_done_execution(
            queries, workspace["id"], job_id, key, kind="code", impl_hash=impl_hash
        )
    c_hash = _publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {}\n")
    queries.update_job_node(job_id, "c", status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "c", kind="code", impl_hash=c_hash)
    queries.update_job_node(job_id, "d", status="completed")
    queries.update_job_status(job_id, "completed")
    # 冻结段只含存活节点（参照 codex4 CRITICAL-1：被删节点的段不播种）。
    _seed_frozen(queries, job_id, ["a", "b", "c"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    (job_dir / "x.json").write_text("stale-rmw")
    (job_dir / "runs" / "d").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "d" / "log.txt").write_text("history")
    _insert_manifest_row(queries, job_id, "d", "x.json")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b/c 全部继承（reset_keys 为空的 all-keep 路径）；被删节点 d 的
    # RMW 名与 runs 目录仍按定义差集清理。
    assert result["kept_node_count"] == 3
    assert statuses == {"a": "completed", "b": "completed", "c": "completed"}
    assert not (job_dir / "x.json").exists()
    assert not (job_dir / "runs" / "d").exists()
    assert _manifest_names(queries, job_id, {"d"}) == set()


# ---------------------------------------------------------------------------
# 4.3 被删节点身份来自 old/new definition 差集（job_nodes 行漂移）
# ---------------------------------------------------------------------------


def test_deleted_node_manifest_rows_cleaned_despite_missing_job_node_row(
    tmp_path: Path,
) -> None:
    """4.3：旧快照有节点 d 但 job_nodes 行缺失（漂移）→ 产物面仍按定义差清理。

    旧缺陷：``renamed_from_nodes`` = 现存 job_nodes 行 − 新节点集——d 的
    行缺失时 renamed 集为空，（d, x_out.json）清单行匹配不到删除面永久
    残留；本地文件 / runs 目录由 definition 差集的 removed_artifact_face
    驱动本就能清，两个面口径不一致。修复：被删节点身份统一来自
    old/new definition 差集。
    """
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", after=["a"], outputs=["b_out.json"]),
            "c": _node("c", after=["b"]),
            "d": _node("d", after=["c"], outputs=["x_out.json"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "b": _node("b", after=["a"], outputs=["b_out.json"]),
            "c": _node("c", after=["b"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c", "d"])
    for key in ("a", "b", "c"):
        impl_hash = _publish_node_code(
            queries, workspace["id"], key, f"def run(ctx):\n    return {{{key!r}}}\n"
        )
        queries.update_job_node(job_id, key, status="pending")
        _seed_done_execution(
            queries, workspace["id"], job_id, key, kind="code", impl_hash=impl_hash
        )
    queries.update_job_node(job_id, "d", status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "b", "c"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    (job_dir / "x_out.json").write_text("stale-x")
    (job_dir / "runs" / "d").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "d" / "log.txt").write_text("history")
    _insert_manifest_row(queries, job_id, "d", "x_out.json")
    # 漂移：旧快照有 d 但 job_nodes 行缺失（如历史清理/手工干预）。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute("delete from job_nodes where job_id=%s and node_key='d'", (job_id,))
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 3
    assert statuses == {"a": "completed", "b": "completed", "c": "completed"}
    # 判别点：清单行按 definition 差集删除（旧代码按现存行推导会漏掉 d）；
    # 文件与 runs 目录（差集驱动的既有面）一并断言。
    assert _manifest_names(queries, job_id, {"d"}) == set()
    assert not (job_dir / "x_out.json").exists()
    assert not (job_dir / "runs" / "d").exists()


# ---------------------------------------------------------------------------
# 4.4 guard 事务内重读 active revision（TOCTOU）+ 整体重试一次
# ---------------------------------------------------------------------------


def _chain_v(capability_b: str) -> WorkflowDefinition:
    return _definition(
        {
            "a": _node("a"),
            "b": _node("b", capability_b, after=["a"]),
            "c": _node("c", after=["b"]),
        }
    )


def _patch_plan_with_publish(monkeypatch, revisions, workspace_id: str, publish_on_calls):
    """monkeypatch plan_inherit_nodes：plan 返回前发布新 revision 模拟竞态。

    返回 plan 调用计数列表（断言重试恰好一次、无第三次尝试）。
    """
    from server.app.services import job_workflow_upgrade_apply as apply_module

    real_plan = apply_module.plan_inherit_nodes
    plan_calls: list[int] = []

    def plan_then_publish(*args, **kwargs):
        plan_calls.append(1)
        result = real_plan(*args, **kwargs)
        if len(plan_calls) in publish_on_calls:
            # 竞争窗口：plan 之后、guard 事务前 active revision 被重发布。
            revisions.publish_workspace_revision(
                workspace_id, _chain_v(f"cap_b_race_{len(plan_calls)}")
            )
        return result

    monkeypatch.setattr(apply_module, "plan_inherit_nodes", plan_then_publish)
    return plan_calls


def test_revision_change_during_guard_retries_with_new_revision(tmp_path, monkeypatch) -> None:
    """4.4 重试成功臂：plan 后发布新 revision → 事务内重读不符 → 整体重试。

    第二次尝试的全部输入（context/plan/frozen/继承集）来自新 active
    revision——job 最终 pin 到重试时的新 revision，而不是第一次 plan 时
    看到的 revision。
    """
    queries, workspace, revisions, original = _setup(tmp_path, _chain_v("cap_b"))
    stale = revisions.publish_workspace_revision(workspace["id"], _chain_v("cap_b_v1"))
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    service = _make_service(tmp_path, queries)
    plan_calls = _patch_plan_with_publish(monkeypatch, revisions, workspace["id"], {1})

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "succeeded"
    # 整体重试恰好一次：第一次 plan 消费旧 active、事务内重读发现漂移
    # 作废；第二次用新 revision 重 plan 后应用成功。
    assert len(plan_calls) == 2
    job = queries.get_job(job_id)
    assert job["workflow_revision_id"] != stale["id"]
    latest = revisions.get_active(workspace["id"], "wfchain")
    assert job["workflow_revision_id"] == latest["id"]
    assert job["workflow_definition_snapshot_json"] == latest["definition_json"]
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"pending"}


def test_revision_change_twice_returns_conflict_without_side_effects(tmp_path, monkeypatch) -> None:
    """4.4 冲突臂：两次尝试都撞上 revision 漂移 → 冲突结果，零副作用。

    第二次重读仍不符时返回 skipped/revision_changed（复用
    upgrade_result 的 reason_code 体系）；job 的 pin、节点状态与
    execution_generation 全部保持升级前原样（两次事务都整体回滚，
    无半应用状态）。
    """
    queries, workspace, revisions, original = _setup(tmp_path, _chain_v("cap_b"))
    revisions.publish_workspace_revision(workspace["id"], _chain_v("cap_b_v1"))
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    before = queries.get_job(job_id)
    service = _make_service(tmp_path, queries)
    plan_calls = _patch_plan_with_publish(monkeypatch, revisions, workspace["id"], {1, 2})

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "revision_changed"
    # 重试仅一次（plan 恰好两次，无第三次尝试）。
    assert len(plan_calls) == 2
    after = queries.get_job(job_id)
    assert after["workflow_revision_id"] == original["id"]
    assert after["execution_generation"] == before["execution_generation"]
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"completed"}
    # 无暂存残留（重读失败先于任何产物暂存）。
    assert not (_job_dir(queries, job_id) / ".staged").exists()
