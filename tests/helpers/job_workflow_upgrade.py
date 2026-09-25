"""Shared seeding helpers for the inherit-mode upgrade test family (#645).

The sibling test modules under ``tests/services/`` (inherit / codex3 / codex4)
must not import each other (boundary: test modules are rename-isolated —
``test_test_modules_do_not_import_each_other``), so the implementation-identity
seeding scaffolding lives here instead, next to ``tests.helpers.seed``.
"""

from __future__ import annotations

import json
import uuid
from contextlib import closing

from server.app.db.connection import connect_database
from server.app.jobs import JobQueries


def seed_done_execution(
    queries: JobQueries,
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    kind: str,
    impl_hash: str,
    skill: str = "",
    skill_version: str = "",
    skill_commit: str = "",
) -> None:
    """播种该节点的一次完成执行：node_run(completed) + done 请求行。

    请求行携带执行时实现身份（``agent_definition_hash``：agent 行是
    Agent 定义哈希、code 行是 code 文本 sha256），与真实 dispatch 链
    （``CodeDispatchService.enqueue`` / ``AgentDispatchService.enqueue``）
    的落库形状一致。skill 三件（codex 五轮 P1-A）镜像 dispatch 的
    ``SkillCheckout.manifest_pins()``：请求行 manifest 携带完整
    ``skill_commit``（mark_done trim 保留该键），node_runs 行携带
    ``skill_version``（``ref@commit12``，v75 列）；默认空串 = 无 skill
    记录（既有用例零改动）。
    """
    run = queries.start_node_run(
        job_id, node_key, ["pi"], "", skill_version=skill_version, skill=skill
    )
    assert run is not None
    queries.finish_node_run(int(run["id"]), "completed", 0, "")
    execution_id = str(uuid.uuid4())
    manifest = {"kind": kind, "node_key": node_key}
    if skill:
        manifest.update(
            {"skill": skill, "skill_version": skill_version, "skill_commit": skill_commit}
        )
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, node_key, kind, agent_id,
              agent_definition_hash, node_concurrency_limit, state,
              queued_at, claimed_at, finished_at, node_run_id, manifest_json)
            values (%s, %s, %s, %s, %s, %s, %s, 1, 'done',
                    current_timestamp, current_timestamp, current_timestamp, %s, %s)
            """,
            (
                execution_id,
                workspace_id,
                job_id,
                node_key,
                kind,
                f"cap_{node_key}",
                impl_hash,
                int(run["id"]),
                json.dumps(manifest),
            ),
        )


def publish_node_code(queries: JobQueries, workspace_id: str, node_key: str, code: str) -> str:
    """发布 node_code 并返回其 code_hash（与 NodeCodeService 同款）。"""

    from server.app.services.node_codes import NodeCodeService

    service = NodeCodeService(queries, custom_nodes_enabled=True)
    service.save_draft(workspace_id, "wfchain", node_key, code, "test")
    row = service.publish(workspace_id, "wfchain", node_key)
    return str(row["code_hash"])


def seed_local_pool_execution(
    queries: JobQueries, job_id: str, node_key: str, impl_hash: str
) -> None:
    """播种本地池形态的完成执行：node_run(completed) 带身份列、无请求行。

    本地 code 池从不写 agent_execution_requests——v85 起身份记录落在
    node_runs（claim_lease 的 insert），这是该路径的播种镜像。
    """
    run = queries.start_node_run(
        job_id, node_key, ["python", "run.py"], "", agent_definition_hash=impl_hash
    )
    assert run is not None
    queries.finish_node_run(int(run["id"]), "completed", 0, "")


def seed_impl_identity(queries, workspace, job_id: str, node_keys) -> None:
    """给拟继承节点播种可证明的实现身份（codex 四轮 P1-1 后的测试基准）。

    普通继承用例的 completed 节点现在还要求「执行时身份 == 当前
    published 身份」：published node_code + 最新完成请求携带同一
    code_hash。不播种的节点按「实现不可证明」保守重跑（P1-1 语义，
    判别用例见 codex4 姊妹文件）。
    """
    for node_key in node_keys:
        code_hash = publish_node_code(
            queries, workspace["id"], node_key, f"def run(ctx):\n    return {{{node_key!r}}}\n"
        )
        queries.update_job_node(job_id, node_key, status="pending")
        seed_done_execution(
            queries, workspace["id"], job_id, node_key, kind="code", impl_hash=code_hash
        )


# ---------------------------------------------------------------------------
# 主题拆分共享的 wfchain 环境构造（codex 复审 P1：inherit 测试文件按主题
# 拆出姊妹文件后，公共的链定义 / 环境 / 播种 / 服务装配集中在这里）
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from server.app.agent_catalog import AgentDefinition  # noqa: E402
from server.app.executors.leases import ExecutorLeaseRepository  # noqa: E402
from server.app.services.job_artifact_mutation import JobArtifactMutationService  # noqa: E402
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService  # noqa: E402
from server.app.services.skill_lock_store import SkillLockStore  # noqa: E402
from server.app.services.workflow_revisions import WorkflowRevisionService  # noqa: E402
from server.app.skills.config import SkillsLock  # noqa: E402
from server.app.skills.manager import SkillManager  # noqa: E402
from server.app.workflows.schema import (  # noqa: E402
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeSkill,
)
from tests.helpers import replace_agent_catalog  # noqa: E402
from tests.postgres_support import TEST_DATABASE_URL  # noqa: E402


def wfchain_definition(outputs_by_node: dict[str, list[str]] | None = None) -> WorkflowDefinition:
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


def wfchain_agent_definition(
    outputs_by_node: dict[str, list[str]] | None = None,
) -> WorkflowDefinition:
    """b 为 agent 节点的三级链（skill 绑定场景）。"""
    import dataclasses

    definition = wfchain_definition(outputs_by_node)
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent")
    return dataclasses.replace(definition, nodes=nodes)


def setup_wfchain_env(tmp_path: Path, definition: WorkflowDefinition):
    """建库连接 + workspace + 首发 revision（wfchain 族公共环境）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    return queries, workspace, revisions, original


def seed_wfchain_job(queries, workspace, original, node_keys) -> str:
    """按 revision 首发快照播种 job 并补 intake 冻结值，返回 job id。"""
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


def make_upgrade_service(
    tmp_path: Path, queries: JobQueries, **kwargs
) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        **kwargs,
    )


def inherit_chain_definition(b_cap: str = "cap_b") -> WorkflowDefinition:
    """a → b → c 三级链（可执行节点，无 start 注入——publish 只吃定义）。"""
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(key="b", label="B", capability=b_cap, after=["a"], config_schema={}),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )


def setup_inherit_env(tmp_path: Path):
    """wfchain 环境 + 裸构造升级服务（无 artifact_mutation/object_store）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], inherit_chain_definition())
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )
    return queries, workspace, revisions, original, service


def seed_inherit_job(queries, workspace, original, node_keys):
    """播种 job（返回整行 dict）并补真实 intake 会冻结的 frozen_config_json。

    （A1 修复后：legacy NULL-frozen 作业的旧侧配置基准不可证明，保守退化
    为全量重跑——常规继承用例必须带上 intake 冻结值才有可继承的旧侧
    基准。）
    """
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
            conn.execute(
                "update jobs set frozen_config_json=%s where id=%s",
                (frozen, job["id"]),
            )
    return job


def put_skill_lock(queries: JobQueries, skills: dict) -> None:
    """把锁文档写进 DB 权威存储（``global_settings.skill_lock``）。

    模拟「另一进程」的 relock（``make skills-lock`` / dispatch 首次 pin）：
    直写 store，不经过任何 SkillManager 的 doc cache——upgrade 判定必须
    读到这里的最新值（#759 P1）。
    """
    SkillLockStore(queries).put_lock(SkillsLock.model_validate({"skills": skills}))


def no_git_spy(monkeypatch) -> list[list[str]]:
    """钉住「upgrade 全链路零 git I/O」：_run_git 被调用即失败。"""
    calls: list[list[str]] = []

    def _spy(self, args, check: bool = True):
        calls.append(list(args))
        raise AssertionError(f"upgrade path must not run git: {args}")

    monkeypatch.setattr(SkillManager, "_run_git", _spy)
    return calls


def seed_reachable_outputs(queries: JobQueries, job_id: str, names: list[str]) -> Path:
    """在 job_dir 播种本地产物文件（可达性基准），返回 job_dir。"""
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (job_dir / name).write_text(f"old-{name}")
    return job_dir


SKILL_KEY = "wschain/sk"
#: 假 commit（upgrade 判定零 git I/O，不需要真实仓库对象）。
COMMIT_V1 = "1" * 40
COMMIT_V2 = "2" * 40


def skill_bound_job(
    tmp_path: Path,
    *,
    node_skill: WorkflowNodeSkill | None = None,
    agent_skill: str = "",
    skill_version: str = "",
    skill_commit: str = "",
):
    """b 为 agent 节点的三级链 + 身份记录完备的 completed job（skill 身份可注入）。"""
    import dataclasses

    definition = wfchain_agent_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    if node_skill is not None:
        nodes = dict(definition.nodes)
        nodes["b"] = dataclasses.replace(nodes["b"], skill=node_skill)
        definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    agent = AgentDefinition(capability="cap_b", runtime="pi", skill=agent_skill)
    replace_agent_catalog(workspace["id"], {"agent-b": agent})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries,
        workspace["id"],
        job_id,
        "b",
        kind="agent",
        impl_hash=agent.definition_hash(),
        skill=SKILL_KEY,
        skill_version=skill_version,
        skill_commit=skill_commit,
    )
    queries.update_job_status(job_id, "completed")
    seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    return queries, workspace, job_id
