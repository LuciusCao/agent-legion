"""codex 第四轮修复的 inherit 升级测试（issue #645，PR #702）。

从 ``test_job_workflow_upgrade_inherit_codex3.py`` 按轮次拆出的姊妹文件
（文件预算）：P1-1 实现身份（执行时 vs 当前 published）、P1-2 旧快照被
移除 output 名与被删节点的清理、P1-3 runtime_mutable 键节点恒重跑。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    publish_node_code as _publish_node_code,
)
from tests.helpers.job_workflow_upgrade import (
    seed_done_execution as _seed_done_execution,
)
from tests.helpers.job_workflow_upgrade import (
    seed_local_pool_execution as _seed_local_pool_execution,
)
from tests.postgres_support import TEST_DATABASE_URL


def _chain(outputs_by_node: dict[str, list[str]] | None = None) -> WorkflowDefinition:
    """a → b → c 三级链，节点 outputs 可注入（实现身份/清理场景公共构造）。"""
    outputs_by_node = outputs_by_node or {}
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(
                key="a", label="A", capability="cap_a", outputs=outputs_by_node.get("a", [])
            ),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                outputs=outputs_by_node.get("b", []),
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )


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
    from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
    from server.app.workflows.definition import workflow_definition_from_dict

    definition = workflow_definition_from_dict(json.loads(original["definition_json"]))
    frozen = intake_frozen_config_json(queries, workspace["id"], definition)
    if frozen is not None:
        with closing(connect_database(queries.dsn_identity)) as conn, conn:
            conn.execute("update jobs set frozen_config_json=%s where id=%s", (frozen, job["id"]))
    return str(job["id"])


def _make_service(tmp_path: Path, queries: JobQueries, **kwargs) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# P1-1 实现身份：执行时身份 vs 当前 published 身份
# ---------------------------------------------------------------------------


def test_impl_identity_matching_keeps_node_inheritable(tmp_path: Path) -> None:
    """codex 四轮 P1-1：执行时身份 == 当前 published 身份 → 节点可继承。

    对照组（判别力基线）：code 节点有 published 实现且最新完成请求的
    code_hash 与当前一致——同 revision 升级时身份可证明，a/b 继承。
    c（b 的下游）无执行记录恒重跑（保守面，见
    test_impl_identity_no_execution_record），判别点：排除不外溢到上游
    ——b 不会因下游 c 重跑而丢继承。
    """
    definition = _chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    queries.update_job_status(job_id, "completed")
    # 产物可达性：a/b 的本地输出文件在位（否则不可达退化会重跑）。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "succeeded"
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b 的执行时身份与当前 published 相等 → 继承（completed 原样）。
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    # c 无执行记录 → 保守重跑；排除不外溢到上游（b 不因 c 重跑而重置）。
    assert statuses["c"] == "pending"


def test_impl_identity_code_republish_reruns_node_and_downstream(tmp_path: Path) -> None:
    """codex 四轮 P1-1：node_code 重发布（节点定义未变）→ 该节点及下游重跑。

    旧缺陷：diff 只比节点定义，两侧哈希相等——旧 job 产物按旧实现产出、
    升级后同节点重跑执行新实现，继承会拿旧实现产物冒充新 revision 产物。
    修复：比较最新完成请求的执行时 code_hash 与当前 published code_hash，
    漂移 → b（及下游 c）重跑，a 无实现记录但……见下一用例（a 无记录恒重跑）。
    本用例聚焦 b/c 的漂移重跑。
    """

    definition = _chain({"b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    old_hash = _publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=old_hash)
    queries.update_job_status(job_id, "completed")
    # b 的产物可达（本地文件在位）——隔离不可达退化，判别点收敛在
    # 实现漂移上：去掉 P1-1 时 b 本应被继承。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-by-v1")
    # b 的实现重发布（code 文本变化、workflow 定义未动）。
    _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 的实现漂移 → b 及下游 c 重跑；a 无执行记录（见下一用例）也重跑。
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    # b 重跑前其旧实现产物进暂存面删除（不会以 v1 字节冒充 v2 产物）。
    assert not (job_dir / "b_out.json").exists()


def test_impl_identity_no_execution_record_reruns_node(tmp_path: Path) -> None:
    """codex 四轮 P1-1：无执行时身份记录（本地池执行 / retention 清扫）→ 恒重跑。

    本地隐含 code 池的 LeaseClaimRequest 不带 node_code、node_runs 无
    code hash 列——本地执行过的节点「旧产物按哪份实现产出」不可证明，
    保守重跑（宁可多跑，不冒旧实现产物冒充的险）。
    """
    definition = _chain()
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 无任何 done 请求行 → 三个节点全部「实现不可证明」→ 全量重跑。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_impl_identity_agent_republish_reruns_node(tmp_path: Path) -> None:
    """codex 四轮 P1-1：Agent 定义重发布（节点定义未变）→ 该节点及下游重跑。

    agent 行的执行时身份是 dispatch 解析到的 Agent definition_hash；
    重发布后当前 published 哈希漂移 → 排除继承。配对断言：身份未漂移
    的 agent 节点照常继承（同 revision、catalog 未变时 b 继承）。
    """
    import dataclasses

    definition = _chain()
    agent_nodes = dict(definition.nodes)
    agent_nodes["b"] = dataclasses.replace(
        definition.nodes["b"], node_type="agent", capability="cap_b"
    )
    definition = dataclasses.replace(definition, nodes=agent_nodes)
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    # Agent 定义重发布：同 capability、config_schema 变化 → definition_hash
    # 漂移。（不携带 skill：skill 绑定由 codex5 的 P1-A 面恒定排除 latest，
    # 这里隔离哈希维度。）
    v2 = AgentDefinition(
        capability="cap_b",
        runtime="pi",
        config_schema={"type": "object", "properties": {"k": {"type": "string"}}},
    )
    replace_agent_catalog(workspace["id"], {"agent-b": v2})
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


# ---------------------------------------------------------------------------
# P1-2 旧快照被移除 output 名 / 被删节点的清理
# ---------------------------------------------------------------------------


def test_removed_output_name_cleaned_local_and_manifest(tmp_path: Path) -> None:
    """codex 四轮 P1-2：重置节点 output 从 old.json 改成 new.json → old.json 清理。

    旧缺陷：stage_outputs 只按新 definition 得暂存名，old.json 不在
    staged_artifact_names——清单行与本地文件保留，API 继续展示旧产物，
    新图同名外部输入还会消费旧字节。修复：从旧快照补出被移除 output 名。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = _chain({"b": ["old.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：b 的 capability 变（进重置面）且 output 改名。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"], capability="cap_b_new", outputs=["new.json"]
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old.json").write_text("stale-bytes")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'old.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/old.json"),
        )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # old.json 的本地文件与清单行都被清理；节点 b 重置（capability 变化）。
    assert result["kept_node_count"] == 0
    assert not (job_dir / "old.json").exists()
    assert not (job_dir / ".staged").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert names == set()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_removed_output_name_of_inherited_sibling_not_cleaned(tmp_path: Path) -> None:
    """codex 四轮 P1-2 配对（A3 口径）：保留节点声明的名字不进清理面。

    a（继承候选）声明 old.json 为输出；b（重置）的旧快照也产出过
    old.json 但新图删掉了它——该名字是 a 的产物/声明面，清掉会把
    completed 节点的清单行指向空文件。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = _chain({"a": ["old.json"], "b": ["old.json", "b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：只有 b 变（capability），a 定义未变 → a 是继承候选。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"], capability="cap_b_new", outputs=["b_out.json"]
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    # a 的实现身份可证明（P1-1）：published node_code + 匹配的完成记录。
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old.json").write_text("a-artifact")
    (job_dir / "b_out.json").write_text("old-b")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (("a", "old.json"), ("b", "old.json"), ("b", "b_out.json")):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job_id, node_key, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # a 继承（old.json 是它的产物——本地文件与清单行原样）；b 重置：
    # b_out.json 走既有暂存路径清理，(b, old.json) 清单行保留（名字属于 a）。
    assert result["kept_node_count"] == 1
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert statuses["a"] == "completed"
    assert (job_dir / "old.json").read_text() == "a-artifact"
    assert not (job_dir / "b_out.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b"})
    assert ("a", "old.json") in names
    assert ("b", "old.json") in names  # 名字被保留节点声明 → 不清理（A3 口径）


def test_deleted_node_outputs_and_runs_cleaned(tmp_path: Path) -> None:
    """codex 四轮 P1-2：生产节点被删 → 其全部纯输出名与 runs 目录清理。

    A4 的 renamed_from_nodes 只按新节点暂存名匹配清单行；节点删除后其
    自身声明名的本地文件与 runs/<key> 历史目录此前全部遗留。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = _chain({"x": ["x_out.json"], "a": [], "b": [], "c": []})
    nodes = dict(old_definition.nodes)
    nodes["x"] = WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"])
    old_definition = dataclasses.replace(old_definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：删除 x。
    new_nodes = {k: v for k, v in old_definition.nodes.items() if k != "x"}
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c", "x"])
    for key in ("a", "b", "c", "x"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x_out.json").write_text("stale-x")
    (job_dir / "runs" / "x").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "x" / "log.txt").write_text("history")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'x', 'x_out.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x_out.json"),
        )
    service = _make_service(tmp_path, queries)

    service.upgrade(workspace["id"], job_id, mode="inherit")

    # 被删节点：清单行、本地产物文件与 runs 历史目录全部清理。
    assert queries.job_artifact_manifest_names_for_nodes(job_id, {"x"}) == set()
    assert not (job_dir / "x_out.json").exists()
    assert not (job_dir / "runs" / "x").exists()
    # runs/x 的暂存件已 commit 删除（.staged/runs 子目录壳是 StagedOutputs
    # 既有行为，与产物清理语义无关，不在此断言）。
    assert not (job_dir / ".staged" / "runs" / "x").exists()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_deleted_node_outputs_cleaned_even_when_rest_inherited(tmp_path: Path) -> None:
    """codex 四轮复审 CRITICAL-1：被删节点的清理独立于重置面。

    只删终端生产节点、其余节点定义未变且身份可证明 → reset_keys 为空
    （all-keep 路径）。旧 guard ``not reset_keys`` 在 removed_artifact_face
    之前早退，被删节点的本地文件 / runs 目录 / 清单行全部遗留——新
    revision 没有任何东西会再生产 x_out.json，但 API 继续把它展示为该
    job 的产物。修法：removed 面非空时即使 reset_keys 为空也走
    stage_outputs（暂存面只含 extra，安全）。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = _chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    nodes = dict(old_definition.nodes)
    # x 放在 a/b/c 之后：job 快照派生定义的节点序与既有用例一致（a/b/c
    # 的继承不受 x 加入节点字典顺序的影响——x 本身不在新定义的可执行集）。
    nodes["x"] = WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"])
    old_definition = dataclasses.replace(old_definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：只删除终端节点 x，a/b 定义未变。
    new_nodes = {k: v for k, v in old_definition.nodes.items() if k != "x"}
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    # a/b/c 的实现身份均可证明（P1-1）——判别点收敛在 CRITICAL-1 的清理面
    # 上：reset_keys 为空（真正的 all-keep 路径），去掉本修复时被删节点 x
    # 的清理被 all-keep guard 短路（文件 / runs / 清单行全部遗留）。
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    c_hash = _publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c", "x"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # _seed_done_execution 置 completed；x 直接置 completed（无执行记录，
    # 但 x 已从新定义消失——被删节点的清理不看身份记录，只看新旧定义差）。
    queries.update_job_node(job_id, "x", status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    _seed_done_execution(queries, workspace["id"], job_id, "c", kind="code", impl_hash=c_hash)
    # 播种 intake 冻结（无 x 的段）：x 不在新定义，其冻结段不该让旧侧
    # 基准判为「有 config 面」而整体退化 clean——直接按 a/b/c 冻结播种
    # 与真实 intake 后删节点的演进序列一致（发布新 revision 前的旧 job
    # 不会带未出生节点的冻结段）。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (
                json.dumps(
                    {
                        "a": {"sandbox_network": False, "timeout_seconds": 600},
                        "b": {"sandbox_network": False, "timeout_seconds": 600},
                        "c": {"sandbox_network": False, "timeout_seconds": 600},
                    }
                ),
                job_id,
            ),
        )
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    (job_dir / "x_out.json").write_text("stale-x")
    (job_dir / "runs" / "x").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "x" / "log.txt").write_text("history")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'x', 'x_out.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x_out.json"),
        )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # a/b/c 全部继承（身份可证明、定义未变、产物可达）——reset_keys 为空
    # 的 all-keep 路径。核心断言：被删节点 x 的清理面完整——文件、runs
    # 目录、清单行，不被 all-keep guard 短路。
    assert result["kept_node_count"] == 3
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert statuses == {"a": "completed", "b": "completed", "c": "completed"}
    assert (job_dir / "a_out.json").read_text() == "old-a"
    assert not (job_dir / "x_out.json").exists()
    assert not (job_dir / "runs" / "x").exists()
    assert queries.job_artifact_manifest_names_for_nodes(job_id, {"x"}) == set()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


# ---------------------------------------------------------------------------
# P1-3 runtime_mutable 键：恒重跑
# ---------------------------------------------------------------------------


def test_runtime_mutable_config_key_node_always_reruns(tmp_path: Path) -> None:
    """codex 四轮 P1-3：含 runtime_mutable 键的节点即使定义未变也重跑。

    runtime_mutable 键每次 dispatch 现场重解析（CONFIG-RUNTIME-MUTABLE-001），
    frozen 段只是 intake 快照——override intake 后改 B、节点按 B 完成、
    升级前改回 A（frozen 值）时新旧 frozen 哈希相等，继承的却是按 B
    产出的产物。保守语义：含此类键的节点不参与继承。
    """
    import dataclasses

    schema = {
        "type": "object",
        "properties": {"dry_run": {"type": "boolean", "default": False, "runtime_mutable": True}},
    }
    definition = _chain({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], config_schema=schema)
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    # 同 revision 内容再发布（定义零变更）——排除规则与 diff 变更无关。
    revisions.publish_workspace_revision(workspace["id"], definition)
    # a/b 的实现身份均可证明（隔离 P1-1 保守面，让判别点收敛在 P1-3 上：
    # 去掉 runtime_mutable 排除时 b 本应被继承）。
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    queries.update_job_status(job_id, "completed")
    # b 的产物可达（本地文件在位）——隔离不可达退化：去掉 P1-3 排除时
    # b 本应被继承，判别点收敛在 runtime_mutable 排除上。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-with-dry-run-B")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 含 runtime_mutable 键 → 恒重跑；下游 c 因上游链哈希含 b 也重跑；
    # a 无 config 面且定义未变 → 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_runtime_mutable_exclusion_covers_upstream_rename_in_old_snapshot() -> None:
    """codex 四轮 P1-3 纯函数：排除面按新 definition 计算，旧快照侧同 key
    节点共用同一判定（排除即恒重跑，不依赖两侧比较）。"""
    from server.app.services.job_workflow_upgrade_diff import (
        compute_inherit_reset_nodes,
        node_is_inherit_excluded,
    )

    schema = {
        "type": "object",
        "properties": {"dry_run": {"type": "boolean", "runtime_mutable": True}},
    }
    mutable_node = WorkflowNode(key="b", label="B", capability="cap_b", config_schema=schema)
    plain_node = WorkflowNode(key="b", label="B", capability="cap_b")

    assert node_is_inherit_excluded(mutable_node)
    assert not node_is_inherit_excluded(plain_node)

    definition = _chain()
    # 同 definition 两侧：含 runtime_mutable 键的 b 仍在重置面。
    reset = compute_inherit_reset_nodes(definition, None, definition, None)
    assert "b" not in reset  # b 无 config_schema → 不排除（对照组）

    nodes = dict(definition.nodes)
    nodes["b"] = mutable_node
    mutable_definition = dataclasses_replace_definition(definition, nodes)
    reset = compute_inherit_reset_nodes(mutable_definition, None, mutable_definition, None)
    assert "b" in reset  # 含 runtime_mutable 键 → 恒重跑


# ---------------------------------------------------------------------------
# 复审 HIGH-2：Agent 定义 schema 的 runtime_mutable 键
# ---------------------------------------------------------------------------


def test_agent_definition_runtime_mutable_key_reruns_node(tmp_path: Path) -> None:
    """复审 HIGH-2：Agent 定义的 config_schema 含 runtime_mutable 键 → 恒重跑。

    攻击路径与 P1-3 针对节点自声明键的同构，只是键声明在 Agent 定义里：
    定义不变 + 只翻转 workspace override 的值再翻回时，frozen 段与实现
    身份（``AgentDefinition.definition_hash()``）两侧全等 → P1-1 不排除、
    节点自声明面（``node.config_schema``）为空——agent 节点带着
    「runtime_mutable 值曾偏离 frozen」的产物被继承。本用例不翻转值、
    只声明键：排除是恒定的，翻转场景是同一判别点的时序化。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    schema = {
        "type": "object",
        "properties": {"dry_run": {"type": "boolean", "default": False, "runtime_mutable": True}},
    }
    definition = _chain({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent", capability="cap_b")
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    # 同 revision 内容再发布（定义零变更）——排除规则与 diff 变更无关。
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    # Agent 定义带 runtime_mutable 键（b 的节点级 config_schema 为空——
    # 旧缺陷：有效 schema 主体在 Agent 定义里，节点自声明判定覆盖不到）。
    # 不携带 skill：latest 绑定在 #759 后恒定排除（codex5 P1-A 面），会
    # 掩盖本用例的 HIGH-2 判别点。
    v1 = AgentDefinition(capability="cap_b", runtime="pi", config_schema=schema)
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # b 的实现身份可证明且未漂移（定义不变、执行时哈希 == 当前哈希）——
    # 隔离 P1-1 保守面，让判别点收敛在 HIGH-2 上：去掉定义侧排除时 b
    # 本应被继承。a 走 code 路径，同样可证明。
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    # a/b 的产物可达（本地文件在位）——隔离不可达退化。
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("produced-with-dry-run-B")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a 身份可证明、定义未变 → 继承；b 的 Agent 定义带 runtime_mutable 键
    # → 恒重跑（其产物按偏离 frozen 的 B 值产出的风险不可证明）；下游 c
    # 因上游链哈希含 b 也重跑。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_agent_definition_without_mutable_keys_stays_inheritable(tmp_path: Path) -> None:
    """复审 HIGH-2 对照组：Agent 定义无 runtime_mutable 键 → 不扩大排除面。

    排除只看定义 schema 的声明：无 runtime_mutable 键、实现身份可证明
    且未漂移的 agent 节点照常继承（证明判别点是键声明本身，而非
    「agent 节点一律排除」的粗面）。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    definition = _chain({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent", capability="cap_b")
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(
        capability="cap_b",
        runtime="pi",
        # 有 schema、但无 runtime_mutable 键（对照组：排除判定不因
        # 「定义携带 schema」而扩大）。不携带 skill：latest 绑定在 #759
        # 后恒定排除（codex5 P1-A 面），会让 b 无法继承、掩盖本对照组的
        # 判别点。
        config_schema={
            "type": "object",
            "properties": {"threshold": {"type": "integer", "default": 1}},
        },
    )
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b 均继承（定义未变、身份可证明）；c 因身份记录缺失重跑
    # （test_impl_identity_matching_keeps_node_inheritable 同款保守面，
    # 与本判别点无关）。
    assert result["kept_node_count"] == 2
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    assert statuses["c"] == "pending"
    assert (job_dir / "b_out.json").read_text() == "old-b"


# ---------------------------------------------------------------------------
# P1-1 补充：本地池执行 hash 记录（#645 v85，node_runs.agent_definition_hash）
# ---------------------------------------------------------------------------


def test_impl_identity_local_pool_record_matching_keeps_node(tmp_path: Path) -> None:
    """#645 v85 主用例：本地池执行有 node_runs 身份记录且匹配 → 可继承。

    旧行为：本地 code 池执行无任何身份记录（请求行不写、node_runs 无列）
    → 恒「不可证明」恒重跑——默认部署的 inherit 退化。修复后 claim 落列，
    记录与当前 published code_hash 相等即继承。判别点：无请求行（本地池
    形态）也可证明；下游无记录仍保守重跑，排除不外溢到上游。
    """
    definition = _chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_local_pool_execution(queries, job_id, "a", a_hash)
    _seed_local_pool_execution(queries, job_id, "b", b_hash)
    queries.update_job_status(job_id, "completed")
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b 的 node_runs 身份与当前 published 相等 → 继承；c 无执行记录 →
    # 保守重跑（本地池形态下没有请求行 fallback 可兜）。
    assert result["kept_node_count"] == 2
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    assert statuses["c"] == "pending"
    assert (job_dir / "a_out.json").read_text() == "old-a"


def test_impl_identity_local_pool_record_drift_reruns_downstream(tmp_path: Path) -> None:
    """#645 v85 漂移用例：node_runs 记录 V1、当前 published V2 → 节点及下游重跑。

    本地池形态（无请求行）：身份漂移经 node_runs 直查暴露，节点进重置面、
    下游沿闭包传播重跑——不再有「本地池执行躲过身份判定」的盲区。
    """
    definition = _chain({"b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    old_hash = _publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_local_pool_execution(queries, job_id, "b", old_hash)
    queries.update_job_status(job_id, "completed")
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-by-v1")
    # b 的实现重发布（workflow 定义未动）→ node_runs 记录的 V1 hash 漂移。
    _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    # b 重跑前其旧实现产物进暂存删除（不会以 v1 字节冒充 v2 产物）。
    assert not (job_dir / "b_out.json").exists()


def test_impl_identity_node_runs_takes_precedence_over_request_row(tmp_path: Path) -> None:
    """#645 v85 合并优先级：node_runs 记录优先于请求行，逐 node_key 二选一。

    b 同时有 v85 前的 done 请求行（记录 V1）与新 node_runs 记录（V2）：
    读取端必须取 node_runs 段（claim 时刻身份、retention 不受窗口影响），
    而不是混行取旧。a 只有请求行（历史 Worker 作业形态）→ fallback 命中。
    """
    definition = _chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    v1_hash = _publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # a：历史形态（只有请求行）；b：请求行记录 V1 + node_runs 记录 V1。
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=v1_hash)
    queries.update_job_status(job_id, "completed")
    # b 重发布 V2：此时补一条带 V2 身份的 node_runs completed 行（模拟
    # v85 后的又一次本地池执行）——node_runs 段最新 completed 记录 V2。
    # （先把 b 拉回 pending：done 请求播种只置 run 行 completed，这里
    # 复位 job_node 以便 start_node_run 的 pending→running 守卫放行。）
    queries.update_job_node(job_id, "b", status="pending")
    _seed_local_pool_execution(
        queries,
        job_id,
        "b",
        _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n"),
    )
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    # b 的产物在位（本地池执行播种不写文件，这里补上——隔离不可达退化，
    # 判别点收敛在读取端的段合并优先级上）。
    (job_dir / "b_out.json").write_text("old-b")
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a 走请求行 fallback、身份匹配 → 继承；b 的 node_runs 段最新记录 V2
    # 与当前 published 相等 → 继承（若误用请求行 V1 会误判漂移重跑）。
    assert result["kept_node_count"] == 2
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    assert statuses["c"] == "pending"
    assert (job_dir / "b_out.json").read_text() == "old-b"


def dataclasses_replace_definition(
    definition: WorkflowDefinition, nodes: dict
) -> WorkflowDefinition:
    import dataclasses

    return dataclasses.replace(definition, nodes=nodes)
