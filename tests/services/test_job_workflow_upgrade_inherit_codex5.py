"""codex 第五轮修复的 inherit 升级测试（issue #645，PR #702）。

从 codex4 姊妹文件按轮次拆出（文件预算）：

- P1-A：skill 绑定按**实际 commit** 判定可继承——执行记录的 skill
  commit（node_runs.skill_version / 请求行 manifest）与当前有效绑定
  解析出的 commit 比较，不可证明一致时保守重跑（legacy fallback 的
  latest 漂移 + 显式 tag 的重解析漂移两个面）；
- P1-B：升级后变成 RMW 输入的旧产物保留（removed_artifact_face 排除
  新节点的 RMW 名，与 rerun 的 #114 语义对齐）；
- P2-C：guard 事务内重验实现身份（plan→mutation TOCTOU，设计 §3 #14
  由 codex 五轮新证据提前纳入）；
- P2-D：无旧快照作业（退化 clean）清理全部旧清单行。
"""

from __future__ import annotations

import dataclasses
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
from server.app.skills.manager import SkillManager
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeSkill,
)
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    publish_node_code as _publish_node_code,
)
from tests.helpers.job_workflow_upgrade import (
    seed_done_execution as _seed_done_execution,
)
from tests.helpers.skill_git import (
    _commit_skill_update,
    _head_commit,
    _make_skill_repo,
    _tag,
)
from tests.helpers.skill_store import memory_skill_store
from tests.postgres_support import TEST_DATABASE_URL

_SKILL_KEY = "wschain/sk"


def _chain(outputs_by_node: dict[str, list[str]] | None = None) -> WorkflowDefinition:
    """a → b → c 三级链，节点 outputs 可注入（codex5 场景公共构造）。"""
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


def _agent_chain(outputs_by_node: dict[str, list[str]] | None = None) -> WorkflowDefinition:
    """b 为 agent 节点的三级链（skill 绑定场景）。"""
    definition = _chain(outputs_by_node)
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent")
    return dataclasses.replace(definition, nodes=nodes)


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


def _make_service(
    tmp_path: Path,
    queries: JobQueries,
    *,
    skill_manager: SkillManager | None = None,
) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        skill_manager=skill_manager,
    )


def _make_skill_manager(tmp_path: Path, lock: dict | None = None) -> SkillManager:
    """测试专用 SkillManager：base_dir/runs_dir 落 tmp，锁文档可播种。"""
    return SkillManager(
        store=memory_skill_store(lock=lock),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )


def _seed_reachable_outputs(queries: JobQueries, job_id: str, names: list[str]) -> Path:
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (job_dir / name).write_text(f"old-{name}")
    return job_dir


# ---------------------------------------------------------------------------
# P1-A：skill 绑定按实际 commit 判定可继承
# ---------------------------------------------------------------------------


def test_skill_legacy_fallback_head_drift_reruns_node_and_downstream(tmp_path: Path) -> None:
    """codex 五轮 P1-A 主场景：Agent definition 的 legacy skill 兜底（latest）。

    旧缺陷：节点未声明 node.skill 而用 ``AgentDefinition.skill`` 时，
    S5 的 skill:latest 排除只查 node.skill 完全不命中——skill 仓库 HEAD
    前进后，节点定义与 Agent definition hash 两侧全等，inherit 保留按旧
    commit 产出的产物，而 dispatch 已会执行新 commit。修复：执行记录的
    skill commit（node_runs.skill_version 的 ref@commit12 / 请求行 manifest
    的 skill_commit）与当前有效绑定解析出的 commit 比较，不可证明一致时
    保守重跑。
    """
    definition = _agent_chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    repo = _make_skill_repo(tmp_path / "skills", key=_SKILL_KEY)
    head_v1 = _head_commit(repo)
    v1 = AgentDefinition(capability="cap_b", runtime="pi", skill=_SKILL_KEY)
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    # b 的执行记录携带 skill 身份：node_runs.skill_version（latest@commit12）
    # + 请求行 manifest 的完整 skill_commit（trim 保留形态）。
    _seed_done_execution(
        queries,
        workspace["id"],
        job_id,
        "b",
        kind="agent",
        impl_hash=v1.definition_hash(),
        skill=_SKILL_KEY,
        skill_version=f"latest@{head_v1[:12]}",
        skill_commit=head_v1,
    )
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    # skill 仓库 HEAD 前进（latest 跟随 HEAD，定义与 Agent hash 均不变）。
    _commit_skill_update(repo, "# skill v2\n")
    service = _make_service(tmp_path, queries, skill_manager=_make_skill_manager(tmp_path))

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 的 skill commit 漂移（latest 跟随新 HEAD）→ b 及下游 c 重跑；
    # a 无 skill 面、身份可证明 → 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_skill_legacy_fallback_matching_commit_keeps_node(tmp_path: Path) -> None:
    """P1-A 对照组：执行时 skill commit 与当前解析一致 → 照常继承。

    证明判别点是 commit 比较本身，而非「带 skill 的 agent 节点一律排除」
    的粗面——HEAD 未动时 latest 解析与执行记录相等，b 继承。
    """
    definition = _agent_chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    repo = _make_skill_repo(tmp_path / "skills", key=_SKILL_KEY)
    head_v1 = _head_commit(repo)
    v1 = AgentDefinition(capability="cap_b", runtime="pi", skill=_SKILL_KEY)
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries,
        workspace["id"],
        job_id,
        "b",
        kind="agent",
        impl_hash=v1.definition_hash(),
        skill=_SKILL_KEY,
        skill_version=f"latest@{head_v1[:12]}",
        skill_commit=head_v1,
    )
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = _make_service(tmp_path, queries, skill_manager=_make_skill_manager(tmp_path))

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # skill commit 匹配 → a/b 继承；c 无执行记录 → 保守重跑（既有语义）。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}


def test_skill_pinned_tag_relock_drift_reruns_node(tmp_path: Path) -> None:
    """P1-A 显式 tag 面：make skills-lock 重解析后锁内 commit 漂移。

    节点显式绑定 ``skill: {key, ref: v1}``（S5 不排除具体 tag）：tag 被
    retag 且锁经 ``make skills-lock`` 重解析后，节点定义与 Agent hash 都
    不变，但 dispatch 解析出的 commit 已漂移——比较面必须命中锁内新
    commit 与执行记录的不一致。
    """
    nodes_override = {
        "b": dataclasses.replace(
            _chain({"b": ["b_out.json"]}).nodes["b"],
            node_type="agent",
            skill=WorkflowNodeSkill(key=_SKILL_KEY, ref="v1"),
        )
    }
    definition = _chain({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = nodes_override["b"]
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    repo = _make_skill_repo(tmp_path / "skills", key=_SKILL_KEY)
    commit_v1 = _tag(repo, "v1")
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries,
        workspace["id"],
        job_id,
        "b",
        kind="agent",
        impl_hash=v1.definition_hash(),
        skill=_SKILL_KEY,
        skill_version=f"v1@{commit_v1[:12]}",
        skill_commit=commit_v1,
    )
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    # tag 重解析：HEAD 前进 + tag force 移动 + 锁内 v1 → 新 commit
    # （make skills-lock 的等价手动形态；真实链路里锁由 CLI 刷新）。
    commit_v2 = _commit_skill_update(repo, "# skill v2\n")
    _tag(repo, "v1", force=True)
    lock = {
        "version": "2",
        "skills": {_SKILL_KEY: {"repo": "", "refs": {"v1": commit_v2}}},
    }
    service = _make_service(tmp_path, queries, skill_manager=_make_skill_manager(tmp_path, lock))

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 锁内 v1 解析到新 commit ≠ 执行记录 commit_v1 → b 及下游 c 重跑。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_skill_no_execution_record_excludes_agent_node(tmp_path: Path) -> None:
    """P1-A 不可证明面：带 skill 绑定但执行记录无 skill commit → 保守重跑。

    v75 前的 node_runs 无 skill_version、请求行 manifest 也无 skill 键时
    （数据态或旧执行），旧产物按哪份 skill 内容产出不可知。
    """
    definition = _agent_chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    _make_skill_repo(tmp_path / "skills", key=_SKILL_KEY)
    v1 = AgentDefinition(capability="cap_b", runtime="pi", skill=_SKILL_KEY)
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    # b 有 Agent 定义哈希记录（P1-1 可证明）但无 skill 身份记录。
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = _make_service(tmp_path, queries, skill_manager=_make_skill_manager(tmp_path))

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 的 skill commit 不可证明 → 重跑（b 及下游 c）；a 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


# ---------------------------------------------------------------------------
# P1-B：升级后变成 RMW 输入的旧产物保留
# ---------------------------------------------------------------------------


def test_upgrade_to_rmw_input_keeps_old_artifact_as_startup_input(tmp_path: Path) -> None:
    """codex 五轮 P1-B：旧 outputs=["x"] → 新 inputs=["x"], outputs=["x"]。

    旧缺陷：``removed_artifact_face`` 的重置节点分支用 ``_pure_outputs(
    new_node)``（outputs − inputs）做差——RMW 名 x 被排除出「新纯输出」
    → 判为已移除产物 → 本地文件进暂存 + 清单行删除 + 对象清理，重置后
    的 RMW 节点缺启动输入（x 没有别的生产者，restore 也无清单可回）。
    修复：清理面排除新节点声明的 RMW 名（新 inputs ∪ outputs 口径），
    与 rerun 保留 RMW 输入的 #114 语义一致。
    """
    old_definition = _chain({"b": ["x.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：b 变更（capability 变化进重置面）且 x 变 RMW。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"],
        capability="cap_b_new",
        inputs=["x.json"],
        outputs=["x.json"],
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _seed_reachable_outputs(queries, job_id, ["x.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b/c 重跑（b 变更）；x.json 是重置后 RMW 节点的启动输入：本地文件
    # 与清单行都保留（重跑后节点原地重写，#114 语义）。
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    assert (job_dir / "x.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_upgrade_to_rmw_input_still_cleans_other_removed_outputs(tmp_path: Path) -> None:
    """P1-B 精度对照：同节点同时有 RMW 化与真正移除的 output 名。

    旧 outputs=[x, y]、新 inputs=[x], outputs=[x]：x 进 RMW 保留（启动
    输入），y 不再被声明 → y 走正常清理面（文件 + 清单行）。证明排除
    面是「新 RMW 名」本身，不是「重置节点带 RMW 即整体跳过」的粗面。
    """
    old_definition = _chain({"b": ["x.json", "y.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"],
        capability="cap_b_new",
        inputs=["x.json"],
        outputs=["x.json"],
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _seed_reachable_outputs(queries, job_id, ["x.json", "y.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for name in ("x.json", "y.json"):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, 'b', %s, %s, 1, 'hash')
                """,
                (job_id, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = _make_service(tmp_path, queries)

    service.upgrade(workspace["id"], job_id, mode="inherit")

    # x（RMW 输入）保留；y（不再声明）清理——文件与清单行都消失。
    assert (job_dir / "x.json").exists()
    assert not (job_dir / "y.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names
    assert ("b", "y.json") not in names


# ---------------------------------------------------------------------------
# P2-C：guard 事务内重验实现身份（plan → mutation TOCTOU）
# ---------------------------------------------------------------------------


def test_guard_revalidates_agent_identity_republished_after_plan(tmp_path: Path) -> None:
    """codex 五轮 P2-C：plan 之后、guard 事务前 Agent 重发布 → 降级重跑。

    旧缺陷（``_published_catalog`` 注释自认）：继承集在事务外规划，
    ``resolve_upgrade_context`` 与 ``lease_guarded_mutation`` 之间 Agent
    定义被重新发布时，guard 只查 lease/running 不验 published 身份——
    事务消费旧继承集，旧实现产物冒充新实现。修复：在受序列化保护的
    应用阶段（guard 事务内）重验实现身份，漂移节点放弃继承（降级重跑，
    与事务内收敛层的 keep ∩ completed + shared_name 复算同一防线风格）。
    """
    definition = _agent_chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    c_hash = _publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    _seed_done_execution(queries, workspace["id"], job_id, "c", kind="code", impl_hash=c_hash)
    queries.update_job_status(job_id, "completed")
    job_dir = _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = _make_service(tmp_path, queries)

    # 竞争窗口：plan_inherit_nodes 内（返回前）重发布 Agent 定义——
    # plan 消费 V1 catalog 得到继承集 {a, b, c}，guard 事务看到的已是 V2。
    from server.app.services import job_workflow_upgrade as upgrade_module

    real_plan = upgrade_module.plan_inherit_nodes

    def plan_then_republish(job_db, job, new_definition, frozen_json, **kwargs):
        inherit = real_plan(job_db, job, new_definition, frozen_json, **kwargs)
        assert {"b", "c"} <= inherit  # plan 时 V1 与下游身份都匹配
        v2 = AgentDefinition(capability="cap_b", runtime="pi", skill="g/n2")
        replace_agent_catalog(workspace["id"], {"agent-b": v2})
        return inherit

    upgrade_module.plan_inherit_nodes = plan_then_republish
    try:
        result = service.upgrade(workspace["id"], job_id, mode="inherit")
    finally:
        upgrade_module.plan_inherit_nodes = real_plan

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # guard 事务内重验：b 的执行时身份（V1）与当前 published（V2）不等
    # → 放弃继承（b 重跑 + 下游 c 重跑）；a 身份未漂移 → 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    # b 的旧产物进暂存删除（不会以 V1 字节冒充 V2 产物）。
    assert not (job_dir / "b_out.json").exists()
    assert (job_dir / "a_out.json").read_text() == "old-a_out.json"


def test_guard_revalidation_no_drift_keeps_planned_inherit(tmp_path: Path) -> None:
    """P2-C 对照组：无 TOCTOU 漂移时重验零影响（继承集不被无谓收缩）。"""
    definition = _agent_chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    _seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 重验与 plan 同源同时刻（无漂移）→ a/b 照常继承；c 无记录保守重跑。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}


# ---------------------------------------------------------------------------
# codex 六轮 P1：RMW 输出纳入同名生产者闭包
# ---------------------------------------------------------------------------


def test_inherit_rmw_output_same_name_as_reset_pure_output_reruns_together(
    tmp_path: Path,
) -> None:
    """codex 六轮 P1：被继承节点声明 RMW（同名为 input+output），重置节点
    把该名声明为普通 output。

    旧缺陷：``shared_name_rerun_closure`` 的名字面用 ``outputs - inputs``
    （纯输出）——RMW 名被完全忽略，RMW 候选不会被拉进重跑面。随后暂存
    移走共享名的本地文件、mutation 只删重置节点清单行、提交后按共享对象
    键删除——被保留的 completed RMW 节点有清单行却无可达产物（本地文件
    在 .staged、权威对象已被 best-effort 清理）。修复：跨继承/重置边界
    的冲突检测覆盖 RMW output（生产者面 = 全部 outputs），RMW 节点与其
    下游一起重跑。

    图：a → rmw(b)，b 声明 inputs=[x.json], outputs=[x.json]；c 重置后
    与 b 共享 x.json（纯输出）。b 是唯一可继承候选（新旧定义与实现身份
    全部可证明）；c 变更进重置面。

    与 P1-B（本文件 ``test_upgrade_to_rmw_input_keeps_old_artifact_as_
    startup_input``）的边界：那是「清理面不删 RMW 节点自己的启动输入」
    （removed_artifact_face 排除 _rmw_names），这是「名字闭包把冲突对方
    拉进重跑」——两个方向互补，不得互相打架。
    """
    old_definition = WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                inputs=["x.json"],
                outputs=["x.json"],
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：c 变更（capability 变化进重置面）并把 x.json 声明为
    # 普通 output——与被继承的 RMW 节点 b 共享对象键。
    new_nodes = dict(old_definition.nodes)
    new_nodes["c"] = dataclasses.replace(
        old_definition.nodes["c"], capability="cap_c_new", outputs=["x.json"]
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    # a/b 的实现身份可证明（publish + 记录一致）→ a/b 都是继承候选；
    # c 变更（S1 种子）进重置面并声明 x.json 为普通 output。
    from tests.helpers.job_workflow_upgrade import (
        seed_local_pool_execution as _seed_local_pool_execution,
    )

    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_local_pool_execution(queries, job_id, "a", a_hash)
    _seed_local_pool_execution(queries, job_id, "b", b_hash)
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _seed_reachable_outputs(queries, job_id, ["x.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 端到端断言：RMW 节点 b 必须被拉进重跑（x.json 与重置节点 c 冲突），
    # 其下游语义随之作废——b/c pending，只有无冲突的 a 保留。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    # b 虽进入重跑面，x.json 仍是它的 RMW 启动输入，必须保留文件与清单
    # 行；否则节点会永久等待一个没有上游重新生产的输入（#114/P1-B）。
    assert (job_dir / "x.json").read_text() == "old-x.json"
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b", "c"})
    assert names == {("b", "x.json")}
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_inherit_rmw_node_without_name_conflict_stays_inherited(tmp_path: Path) -> None:
    """codex 六轮 P1 对照组：RMW 节点的名字无跨边界冲突 → 照常继承。

    证明判别点是「冲突检测覆盖 RMW output」，而非「RMW 节点一律排除」
    的粗面——重置节点不声明 x.json 时，b 的 RMW 语义（启动输入保留，
    #114/P1-B）原样成立：本地文件与清单行保留、节点 completed。
    """
    old_definition = WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                inputs=["x.json"],
                outputs=["x.json"],
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision：c 变更但输出名与 b 的 RMW 名不冲突。
    new_nodes = dict(old_definition.nodes)
    new_nodes["c"] = dataclasses.replace(
        old_definition.nodes["c"], capability="cap_c_new", outputs=["c_out.json"]
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    from tests.helpers.job_workflow_upgrade import (
        seed_local_pool_execution as _seed_local_pool_execution,
    )

    a_hash = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = _publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    _seed_local_pool_execution(queries, job_id, "a", a_hash)
    _seed_local_pool_execution(queries, job_id, "b", b_hash)
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = _seed_reachable_outputs(queries, job_id, ["x.json", "c_out.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b 身份均可证明且 b 的 RMW 名无冲突 → 两者继承；c 变更重跑。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}
    assert (job_dir / "x.json").read_text() == "old-x.json"
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names


# ---------------------------------------------------------------------------
# P2-D：无旧快照作业（退化 clean）清理全部旧清单行
# ---------------------------------------------------------------------------


def test_degraded_clean_clears_all_legacy_manifest_rows(tmp_path: Path) -> None:
    """codex 五轮 P2-D：legacy 作业无快照 → 退化全量重跑 + 清空旧清单行。

    旧缺陷：快照解析失败时规划层退化 clean（全量重跑），但
    ``removed_artifact_face`` 对 None 旧快照直接返回空面、暂存面只按新
    definition 的 outputs 收集——旧节点/改名输出的 ``job_artifacts`` 行
    残留（产物 API 继续展示 + 对象存储权威引用悬挂）。修复：无任何继承
    节点时，事务内删除该 job 的全部旧产物清单行并安排对象清理（退化
    clean = 旧产物全部作废，与 A1/S7 的退化语义对齐）。
    """
    definition = _chain({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(
        workspace["id"],
        dataclasses.replace(
            definition,
            nodes={
                **definition.nodes,
                "b": dataclasses.replace(definition.nodes["b"], capability="cap_b_new"),
            },
        ),
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    _seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    # legacy 作业：快照损坏（不可解析）→ plan 退化 clean。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json='{bad json' where id=%s",
            (job_id,),
        )
        # 旧节点 key 的清单行（含 rename 前身份形态——新定义里没有 'old'）。
        for node_key, name in (
            ("a", "a_out.json"),
            ("b", "b_out.json"),
            ("old", "renamed_out.json"),
        ):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job_id, node_key, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 退化 clean：全量重跑（kept=0）；全部旧清单行（含旧 key 的孤儿行）
    # 同事务删除——产物 API 不再展示旧 revision 产物。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b", "c", "old"})
    assert names == set()
