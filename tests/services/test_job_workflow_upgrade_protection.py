"""issue #759 复审 P1-A：upgrade 输入保护计划的 reset-aware liveness/freshness 判定。

旧缺陷（codex P1-A 最小反例）：``P(outputs=["x"])`` 纯产、``C(inputs=["x"])``
纯消费、两者间无显式边且均被本次升级 reset——旧 ``unprotected_input_names``
判 x 时禁用 x 自己的隐式消费边，x 永远证不出「可由重置的 P 重新生成」而落入
keep 集：clean/全退化分支暂存了本地 x 字节却保留旧 ``job_artifacts`` 清单行，
ready 前 hydration（``workflow_worker/input_hydration.py``）立刻复活旧字节，
C 在 P 重跑前消费旧 revision 产物。

新模型（``input_protection_plan``，纯函数，输入含 keep/reset 节点集与本次
删除面）同时证明两个方向，任一不可证明即 fail closed：

- liveness：名字在新一轮执行中会变得可用——保留节点产物、外部输入（无生产
  者）、或存在可运行的重置生产者会重生成（Ava 最小不动点；循环互依赖的生产
  者证不出可运行 ⇒ 删除会导致永久等待）；
- freshness：非 RMW 名三面删除后「缺席即闸」——consumer 的 ready gate 探本地
  文件，名字缺席 ⇒ consumer 必然等到重置生产者重写；RMW 附着名的旧文件不进
  暂存面（#114）而存活，每个 consumer 必须有排序证据（显式边 ∪ 经由「唯一
  生产者且本次会缺席」名字的隐式边）保证在重置生产者之后执行；
- 保留侧同样要证：有重置纯生产者作废 x 时，保留旧字节给纯 consumer 吃是静默
  错误——RMW 启动输入（无纯 consumer 风险）才可保留。

前半文件是 ``input_protection_plan`` 的表驱动纯函数用例（不触库），后半是
upgrade 服务级集成用例（本地文件 / 清单行 / 权威对象三面断言）。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.job_workflow_upgrade_protection import input_protection_plan
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
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


def _definition(
    nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge] | None = None
) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="wfchain", label="Wf Chain", intake=WorkflowIntake(), nodes=nodes, edges=edges or []
    )


# ---------------------------------------------------------------------------
# 表驱动纯函数用例：input_protection_plan 的 keep / clean / unprovable 三分区
# ---------------------------------------------------------------------------


def _plan(
    definition: WorkflowDefinition,
    *,
    reset: set[str],
    keep: set[str] | None = None,
    staged: set[str] | None = None,
):
    return input_protection_plan(
        definition,
        keep_nodes=frozenset(keep or set()),
        reset_nodes=frozenset(reset),
        staged_names=frozenset(staged or set()),
    )


#: 每条用例：(id, definition, keep, reset, staged, 期望 keep/clean/unprovable)。
#: staged 列即「本次删除面」输入（调用方 staging_output_names 的计算结果）。
_PLAN_CASES = [
    # codex P1-A 最小反例：纯文件名关联（无显式边）、双方均 reset ⇒ x 三面清理。
    (
        "implicit_only_counterexample",
        _definition({"p": _node("p", outputs=["x.json"]), "c": _node("c", inputs=["x.json"])}),
        set(),
        {"p", "c"},
        {"x.json"},
        (set(), {"x.json"}, set()),
    ),
    # 同反例 + 无关保留节点：keep 集存在不改变 x 的缺席判定（reset-aware）。
    (
        "implicit_only_with_kept_node",
        _definition(
            {
                "a": _node("a", outputs=["a_out.json"]),
                "p": _node("p", outputs=["x.json"]),
                "c": _node("c", inputs=["x.json"]),
            }
        ),
        {"a"},
        {"p", "c"},
        {"x.json"},
        (set(), {"x.json"}, set()),
    ),
    # RMW 对照组（验收 3）：q 是 x 的 RMW、p 纯产 x 但排在 q 之后（q→p）——
    # 保留旧 x 是 q 首跑的启动输入，且无纯 consumer 吃旧字节 ⇒ keep 可证。
    (
        "rmw_startup_kept_when_producer_runs_after",
        _definition(
            {
                "a": _node("a", outputs=["a_out.json"]),
                "q": _node("q", after=["a"], inputs=["x.json"], outputs=["x.json"]),
                "p": _node("p", after=["q"], outputs=["x.json"]),
            }
        ),
        {"a"},
        {"p", "q"},
        set(),
        ({"x.json"}, set(), set()),
    ),
    # 显式边链放行：p→q→c 全显式，RMW 名 x 的全部 consumer 被排序证据覆盖。
    (
        "explicit_chain_cleans_rmw_name",
        _definition(
            {
                "p": _node("p", outputs=["x.json"]),
                "q": _node("q", after=["p"], inputs=["x.json"], outputs=["x.json"]),
                "c": _node("c", after=["q"], inputs=["x.json"]),
            }
        ),
        set(),
        {"p", "q", "c"},
        set(),
        (set(), {"x.json"}, set()),
    ),
    # 循环互证（验收 4）：px/py 互等对方产物 ⇒ liveness 证不出 ⇒ fail closed。
    (
        "producer_cycle_unprovable",
        _definition(
            {
                "px": _node("px", inputs=["y.json"], outputs=["x.json"]),
                "py": _node("py", inputs=["x.json"], outputs=["y.json"]),
                "c": _node("c", inputs=["x.json"]),
            }
        ),
        set(),
        {"px", "py", "c"},
        {"x.json", "y.json"},
        (set(), set(), {"x.json", "y.json"}),
    ),
    # 跨名互借（复审 P1 互证反例）：x/w 都是 RMW 附着名（旧文件存活），c 无
    # 显式入边 ⇒ 留则 c 吃旧字节、删则文件仍在同样吃到 ⇒ 两方向均不可证明。
    (
        "cross_name_rmw_borrow_unprovable",
        _definition(
            {
                "p": _node("p", outputs=["x.json", "w.json"]),
                "q": _node("q", after=["p"], inputs=["x.json"], outputs=["x.json"]),
                "m": _node("m", after=["p"], inputs=["w.json"], outputs=["w.json"]),
                "c": _node("c", inputs=["x.json", "w.json"]),
            }
        ),
        set(),
        {"p", "q", "m", "c"},
        set(),
        (set(), set(), {"w.json", "x.json"}),
    ),
    # 隐式缺席证据链（非单点特判的证明）：c 无 px 侧显式路径，但借 z 的隐式
    # 边（z 非 RMW、唯一生产者 px、本次三面删除）⇒ c 保证在 px 之后 ⇒ 放行。
    (
        "implicit_absence_chain_cleans_rmw_name",
        _definition(
            {
                "px": _node("px", outputs=["x.json", "z.json"]),
                "q": _node("q", after=["px"], inputs=["x.json"], outputs=["x.json"]),
                "c": _node("c", inputs=["x.json", "z.json"]),
            }
        ),
        set(),
        {"px", "q", "c"},
        {"z.json"},
        (set(), {"x.json", "z.json"}, set()),
    ),
    # 多生产者名字的隐式边不作排序证据：c 可能只等到 p2 的重写（px 未跑），
    # 读到的 x 仍是旧字节 ⇒ freshness 不可证 ⇒ fail closed。
    (
        "multi_producer_edge_is_not_ordering_evidence",
        _definition(
            {
                "px": _node("px", outputs=["x.json", "z.json"]),
                "p2": _node("p2", outputs=["z.json"]),
                "q": _node("q", after=["px"], inputs=["x.json"], outputs=["x.json"]),
                "c": _node("c", inputs=["x.json", "z.json"]),
            }
        ),
        set(),
        {"px", "p2", "q", "c"},
        {"z.json"},
        (set(), {"z.json"}, {"x.json"}),
    ),
    # 纯 consumer 被显式边覆盖 + RMW consumer 未覆盖 ⇒ keep（启动输入保留，
    # 纯 consumer 仍等 p 重写后拿新字节）。
    (
        "covered_pure_plus_uncovered_rmw_keeps",
        _definition(
            {
                "p": _node("p", outputs=["x.json"]),
                "q": _node("q", inputs=["x.json"], outputs=["x.json"]),
                "c": _node("c", after=["p"], inputs=["x.json"]),
            }
        ),
        set(),
        {"p", "q", "c"},
        set(),
        ({"x.json"}, set(), set()),
    ),
    # 外部输入（无生产者）⇒ keep：清单行是唯一的回填来源（#114）。
    (
        "external_input_kept",
        _definition({"c": _node("c", inputs=["e.json"])}),
        set(),
        {"c"},
        set(),
        ({"e.json"}, set(), set()),
    ),
    # 保留节点的声明面（A3 口径）：x 由保留节点产出 ⇒ keep，重置 consumer 继续
    # 读保留节点的有效产物。
    (
        "kept_producer_name_kept",
        _definition({"a": _node("a", outputs=["x.json"]), "c": _node("c", inputs=["x.json"])}),
        {"a"},
        {"c"},
        set(),
        ({"x.json"}, set(), set()),
    ),
    # 缺席证据必须在本次删除面内：非 RMW 名不在暂存面（删除面漂移）⇒ 无法
    # 保证缺席 ⇒ fail closed（防御分支，正常收敛后不可达）。
    (
        "absence_requires_deletion_face",
        _definition({"p": _node("p", outputs=["x.json"]), "c": _node("c", inputs=["x.json"])}),
        set(),
        {"p", "c"},
        set(),
        (set(), set(), {"x.json"}),
    ),
    # 复审增补 P1 回归：edge.condition.artifact 是消费面——条件产物由与边
    # 不相邻的 scorer 生产，gated target 经 artifact_consumption_index 进入
    # 消费索引；三面删除 ⇒ clean（缺席即闸：分支评估等 scorer 重写）。
    (
        "condition_artifact_consumer_covered",
        _definition(
            {
                "scorer": _node("scorer", outputs=["verdict.json"]),
                "entry": _node("entry"),
                "gated": _node("gated"),
            },
            [
                WorkflowEdge(source="entry", target="gated"),
                WorkflowEdge(
                    source="entry",
                    target="gated",
                    condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
                ),
            ],
        ),
        set(),
        {"scorer", "entry", "gated"},
        {"verdict.json"},
        (set(), {"verdict.json"}, set()),
    ),
    # 同形态但条件产物不在本次删除面 ⇒ 缺席不可证 ⇒ fail closed（绝不能
    # 静默漏出保护计划，否则旧条件字节残留 = 分支评估走错分支）。
    (
        "condition_artifact_unstaged_unprovable",
        _definition(
            {
                "scorer": _node("scorer", outputs=["verdict.json"]),
                "entry": _node("entry"),
                "gated": _node("gated"),
            },
            [
                WorkflowEdge(
                    source="entry",
                    target="gated",
                    condition=WorkflowCondition(artifact="verdict.json", path="$.ok", equals=True),
                ),
            ],
        ),
        set(),
        {"scorer", "entry", "gated"},
        set(),
        (set(), set(), {"verdict.json"}),
    ),
]


@pytest.mark.parametrize(
    "definition,keep,reset,staged,expected",
    [case[1:] for case in _PLAN_CASES],
    ids=[case[0] for case in _PLAN_CASES],
)
def test_input_protection_plan_table(
    definition: WorkflowDefinition,
    keep: set[str],
    reset: set[str],
    staged: set[str],
    expected: tuple[set[str], set[str], set[str]],
) -> None:
    plan = _plan(definition, keep=keep, reset=reset, staged=staged)

    exp_keep, exp_clean, exp_unprovable = expected
    assert plan.keep == frozenset(exp_keep)
    assert plan.clean == frozenset(exp_clean)
    assert plan.unprovable == frozenset(exp_unprovable)


# ---------------------------------------------------------------------------
# upgrade 服务级集成用例：三面（本地文件 / 清单行 / 权威对象）断言
# ---------------------------------------------------------------------------


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
    import json

    section = {"sandbox_network": False, "timeout_seconds": 600}
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({key: dict(section) for key in node_keys}), job_id),
        )


class _RecordingObjectStore:
    """对象存储桩：记录 delete_objects 收到的 storage_key。"""

    enabled = True

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def live_keys_for(self, job_id: str, keys: list[str]) -> set[str]:
        return set()

    def delete_objects(self, rows: list[dict]) -> None:
        self.deleted.extend(str(row["storage_key"]) for row in rows)


def _make_service(
    tmp_path: Path, queries: JobQueries, *, object_store=None
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


def _counterexample_definitions() -> tuple[WorkflowDefinition, WorkflowDefinition]:
    """codex P1-A 最小反例：p 纯产 x、c 纯消费 x、无显式边；新 revision 换 capability。"""
    old = _definition({"p": _node("p", outputs=["x.json"]), "c": _node("c", inputs=["x.json"])})
    new = _definition(
        {
            "p": _node("p", "cap_p_new", outputs=["x.json"]),
            "c": _node("c", "cap_c_new", inputs=["x.json"]),
        }
    )
    return old, new


def _seed_counterexample_job(
    tmp_path: Path,
) -> tuple[JobQueries, dict, WorkflowDefinition, str, Path, str]:
    """播种反例的 completed job（旧 x 字节 + 清单行），发布新 revision。"""
    old, new = _counterexample_definitions()
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["p", "c"])
    for key in ("p", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_text("old-x")
    x_key = _insert_manifest_row(queries, job_id, "p", "x.json")
    return queries, workspace, new, job_id, job_dir, x_key


def test_clean_counterexample_deletes_three_faces(tmp_path: Path) -> None:
    """验收 1/2（clean 分支）：旧 x 的本地文件、清单行、权威对象三面失效。

    旧行为：x 落入 keep 集——本地文件被暂存但清单行保留（对象也不删），
    hydration 在 ready 前复活旧字节。
    """
    queries, workspace, _, job_id, job_dir, x_key = _seed_counterexample_job(tmp_path)
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="clean")

    assert result["status"] == "succeeded"
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"p", "c"}) == set()
    assert x_key in store.deleted


def test_degenerate_inherit_counterexample_deletes_three_faces(tmp_path: Path) -> None:
    """验收 2（inherit 全退化分支）：旧快照损坏 ⇒ 退化全量重跑，三面同样清理。"""
    queries, workspace, _, job_id, job_dir, x_key = _seed_counterexample_job(tmp_path)
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json='{bad json' where id=%s",
            (job_id,),
        )
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"p", "c"}) == set()
    assert x_key in store.deleted


def test_inherit_with_kept_node_counterexample_deletes_three_faces(tmp_path: Path) -> None:
    """验收 2（inherit 有保留节点分支）：暂存名路径的三面清理回归钉住。"""
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "p": _node("p", outputs=["x.json"]),
            "c": _node("c", inputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "p": _node("p", "cap_p_new", outputs=["x.json"]),
            "c": _node("c", "cap_c_new", inputs=["x.json"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "p", "c"])
    # a 的实现身份可证明 ⇒ 继承保留（P1-1 基准）。
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    queries.update_job_node(job_id, "a", status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    for key in ("p", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "p", "c"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("old-x")
    # codex #776 复审 P2：权威层启用时保留节点的每个 output 都要清单行
    # （本地文件只是可淘汰缓存），a 的可达性证据补清单行。
    _insert_manifest_row(queries, job_id, "a", "a_out.json")
    x_key = _insert_manifest_row(queries, job_id, "p", "x.json")
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "p": "pending", "c": "pending"}
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"p", "c"}) == set()
    assert x_key in store.deleted


def test_rmw_startup_control_keeps_three_faces(tmp_path: Path) -> None:
    """验收 3（RMW 对照组）：q 是 x 的 RMW、p 纯产 x 但排在 q 之后 ⇒ 保留。

    删行会让 q 首跑的启动输入失去 hydration 回填来源（本地淘汰后永久等待）；
    此处无纯 consumer，保留旧字节不构成 freshness 风险。
    """
    old = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "q": _node("q", after=["a"], inputs=["x.json"], outputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "a": _node("a", outputs=["a_out.json"]),
            "q": _node("q", "cap_q_new", after=["a"], inputs=["x.json"], outputs=["x.json"]),
            "p": _node("p", after=["q"], outputs=["x.json"]),
        }
    )
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "q"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    queries.update_job_node(job_id, "a", status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    queries.update_job_node(job_id, "q", status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "q"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("startup-x")
    # codex #776 复审 P2：权威层启用时保留节点的每个 output 都要清单行。
    _insert_manifest_row(queries, job_id, "a", "a_out.json")
    _insert_manifest_row(queries, job_id, "q", "x.json")
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    # 三面全保留：本地文件、清单行、对象（不删）。
    assert (job_dir / "x.json").read_text() == "startup-x"
    assert ("q", "x.json") in _manifest_names(queries, job_id, {"q"})
    assert store.deleted == []


def _cycle_definitions() -> tuple[WorkflowDefinition, WorkflowDefinition]:
    """循环互证：px 等 y、py 等 x——两个名的 liveness 互相证不出。"""
    old = _definition(
        {
            "px": _node("px", inputs=["y.json"], outputs=["x.json"]),
            "py": _node("py", inputs=["x.json"], outputs=["y.json"]),
            "c": _node("c", after=["px"], inputs=["x.json"]),
        }
    )
    new = _definition(
        {
            "px": _node("px", "cap_px_new", inputs=["y.json"], outputs=["x.json"]),
            "py": _node("py", "cap_py_new", inputs=["x.json"], outputs=["y.json"]),
            "c": _node("c", "cap_c_new", after=["px"], inputs=["x.json"]),
        }
    )
    return old, new


def test_cycle_unprovable_fails_closed_clean(tmp_path: Path) -> None:
    """验收 4（clean）：循环互证 ⇒ skipped/protection_unprovable，零副作用。"""
    old, new = _cycle_definitions()
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["px", "py", "c"])
    for key in ("px", "py", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_text("old-x")
    (job_dir / "y.json").write_text("old-y")
    _insert_manifest_row(queries, job_id, "px", "x.json")
    _insert_manifest_row(queries, job_id, "py", "y.json")
    before = queries.get_job(job_id)
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="clean")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "protection_unprovable"
    assert "x.json" in result["message"] and "y.json" in result["message"]
    # 零副作用：节点状态、代次、文件、清单行、对象全部原样。
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"completed"}
    after = queries.get_job(job_id)
    assert after["execution_generation"] == before["execution_generation"]
    assert after["workflow_revision_id"] == original["id"]
    assert (job_dir / "x.json").read_text() == "old-x"
    assert (job_dir / "y.json").read_text() == "old-y"
    assert _manifest_names(queries, job_id, {"px", "py"}) == {
        ("px", "x.json"),
        ("py", "y.json"),
    }
    assert store.deleted == []
    assert not (job_dir / ".staged").exists()


def test_cycle_unprovable_fails_closed_inherit(tmp_path: Path) -> None:
    """验收 4（inherit 带保留节点）：收敛后的 keep/reset 集上同样 fail closed。"""
    old, new = _cycle_definitions()
    old = _definition({**old.nodes, "a": _node("a", outputs=["a_out.json"])})
    new = _definition({**new.nodes, "a": _node("a", outputs=["a_out.json"])})
    queries, workspace, revisions, original = _setup(tmp_path, old)
    revisions.publish_workspace_revision(workspace["id"], new)
    job_id = _seed_job(queries, workspace, original, ["a", "px", "py", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    queries.update_job_node(job_id, "a", status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    for key in ("px", "py", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_frozen(queries, job_id, ["a", "px", "py", "c"])
    job_dir = _job_dir(queries, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "x.json").write_text("old-x")
    (job_dir / "y.json").write_text("old-y")
    _insert_manifest_row(queries, job_id, "px", "x.json")
    _insert_manifest_row(queries, job_id, "py", "y.json")
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "skipped"
    assert result["reason_code"] == "protection_unprovable"
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"completed"}
    assert (job_dir / "x.json").read_text() == "old-x"
    assert (job_dir / "y.json").read_text() == "old-y"
    assert _manifest_names(queries, job_id, {"px", "py"}) == {
        ("px", "x.json"),
        ("py", "y.json"),
    }
    assert store.deleted == []
    assert not (job_dir / ".staged").exists()
    assert store.deleted == []
    assert not (job_dir / ".staged").exists()


def test_post_commit_sweep_removes_hydration_resurrected_file(tmp_path: Path, monkeypatch) -> None:
    """#759 复审 P1-A 残余窗口：提交前复活的旧字节文件由提交后 sweep 收掉。

    模拟 hydration 的残余窗口（恢复写完成、代次复查通过、随后突变才提交）：
    在升级事务内（暂存之后、提交之前）把旧 x 字节写回 job_dir——正是
    hydration 恢复写在突变提交前落地的形态。提交后的 sweep 必须把它再删
    一次——否则下一轮评估 ready gate 会拿旧字节放行 c。
    """
    queries, workspace, _, job_id, job_dir, x_key = _seed_counterexample_job(tmp_path)
    store = _RecordingObjectStore()
    service = _make_service(tmp_path, queries, object_store=store)
    from server.app.services import job_workflow_upgrade_apply as apply_module

    real_mutation = apply_module.upgrade_job_workflow_inherit

    def resurrect_during_transaction(conn, job_id_arg, **kwargs):
        out = real_mutation(conn, job_id_arg, **kwargs)
        # 残余窗口产物：hydration 恢复写在事务提交前落盘（代次复查通过）。
        (job_dir / "x.json").write_text("old-x")
        return out

    monkeypatch.setattr(apply_module, "upgrade_job_workflow_inherit", resurrect_during_transaction)

    result = service.upgrade(workspace["id"], job_id, mode="clean")

    assert result["status"] == "succeeded"
    # sweep 把复活的旧文件删掉：三面终态仍是缺席。
    assert not (job_dir / "x.json").exists()
    assert _manifest_names(queries, job_id, {"p", "c"}) == set()
    assert x_key in store.deleted
