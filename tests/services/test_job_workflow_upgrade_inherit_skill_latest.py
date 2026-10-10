"""skill:latest 继承精确比对的验收测试（issue #1148 方向 1 定稿）。

#759 起的最新语义（恒定排除）→ #1148：plan 阶段对 latest 绑定解析当前
HEAD（有界 rev-parse，``job_workflow_upgrade_skill_heads``），执行记录
commit == HEAD 即可继承；HEAD 前进 / 解析失败（None）/ 记录缺失 →
保守重置。guard 事务内重验沿用 plan 的 HEAD 常量（零 git 子进程，#759
P1）。pinned 分支行为零变化由既有用例承重
（``test_job_workflow_upgrade_inherit_skill.py``）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowNodeSkill,
)
from tests.helpers.job_workflow_upgrade import (
    SKILL_KEY,
    make_upgrade_service,
    skill_bound_job,
)


def _git(repo: Path, *args: str) -> str:
    """与 ``tests.helpers.skill_git`` 同款：隔离 GIT_DIR 等父仓库变量。"""
    env = {
        **dict(os.environ),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _make_skill_repo(base_dir: Path) -> Path:
    """在 ``<base_dir>/<SKILL_KEY>`` 建 in-place git 仓库（#322 唯一模式）。"""
    repo = base_dir / SKILL_KEY
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "SKILL.md").write_text("# skill v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-gpg-sign")
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _advance_head(repo: Path) -> str:
    """改写 SKILL.md 并提交，返回推进后的新 HEAD。"""
    (repo / "SKILL.md").write_text("# skill v2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update", "--no-gpg-sign")
    return _git(repo, "rev-parse", "HEAD")


def _patch_skill_root(monkeypatch, base_dir: Path) -> None:
    """把 skill root 指到测试沙箱（默认 ``~/.agents/skills`` 不可注入）。"""
    from server.app.services import job_workflow_upgrade_skill_heads as heads

    monkeypatch.setattr(heads, "default_skill_base_dir", lambda: base_dir)


def _statuses(queries, job_id: str) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}


def test_latest_head_unchanged_inherits_node(tmp_path: Path, monkeypatch) -> None:
    """执行记录 commit == 当前 HEAD（两次 revision 间 HEAD 未动）→ 继承。

    #1148 主收益场景：仅差一个 skill pin 的两次 revision 升级，latest
    agent 节点不再被无条件重置重跑。
    """
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{head[:12]}",
        skill_commit=head,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # a/b 继承（b 的 latest 绑定 proven）；c 无执行记录 → 保守重跑。
    assert result["kept_node_count"] == 2
    assert _statuses(queries, job_id) == {"a": "completed", "b": "completed", "c": "pending"}


def test_latest_head_advanced_reruns_node(tmp_path: Path, monkeypatch) -> None:
    """仓库 HEAD 前进过（执行记录 commit != HEAD）→ 重置重跑。"""
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    old_head = _head(repo)
    new_head = _advance_head(repo)
    assert new_head != old_head
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{old_head[:12]}",
        skill_commit=old_head,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 当前 HEAD != 执行记录 → b 及下游 c 重跑；a 继承。
    assert result["kept_node_count"] == 1
    assert _statuses(queries, job_id) == {"a": "completed", "b": "pending", "c": "pending"}


def test_latest_head_prefix_record_inherits_node(tmp_path: Path, monkeypatch) -> None:
    """前缀形态：执行记录只有 node_runs 的 ``latest@commit12`` 时按前缀
    等长截断比较（与 pinned 分支同语义）。"""
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{head[:12]}",
        skill_commit="",  # 请求行 manifest 无完整 sha → 回落 version 前缀
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["kept_node_count"] == 2
    assert _statuses(queries, job_id) == {"a": "completed", "b": "completed", "c": "pending"}


def test_latest_rev_parse_failure_conservative_rerun(tmp_path: Path, monkeypatch) -> None:
    """rev-parse 失败（skill 仓库缺失 → None）→ 保守重置且不 500。"""
    base_dir = tmp_path / "skills"
    base_dir.mkdir()  # root 存在但 <SKILL_KEY> 仓库缺失
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{'1' * 12}",
        skill_commit="1" * 40,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert _statuses(queries, job_id) == {"a": "completed", "b": "pending", "c": "pending"}


def test_latest_legacy_fallback_matching_head_inherits_node(tmp_path: Path, monkeypatch) -> None:
    """legacy 兜底（AgentDefinition.skill，ref 恒 latest）同享精确比对。

    兜底绑定逃过 S5（节点不显式声明），由 P1-A skill 面的 latest_commits
    精确比对覆盖：匹配 → 继承（此前恒排除）。
    """
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        agent_skill=SKILL_KEY,
        skill_version=f"latest@{head[:12]}",
        skill_commit=head,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    assert result["kept_node_count"] == 2
    assert _statuses(queries, job_id) == {"a": "completed", "b": "completed", "c": "pending"}


def test_guard_revalidation_reuses_plan_head_constant(tmp_path: Path, monkeypatch) -> None:
    """guard 重验沿用 plan 的 HEAD 常量：plan 之后 HEAD 前进不触发已继承
    节点重跑（残余窗口无害性，与 pinned ref 的 relock 窗口同构）。

    旧缺陷形态（若 guard 重新 rev-parse）：plan 消费旧 HEAD 得出继承集，
    事务内重验读到推进后的新 HEAD ≠ 执行记录 → b 放弃继承。正确行为：
    重验只做纯 DB 读 + 字符串比较（含 plan 传入的 HEAD 常量），HEAD
    事后前进对本次升级不可见——dispatch 下次执行自然消费新 HEAD
    （latest 永不入锁，#322）。
    """
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{head[:12]}",
        skill_commit=head,
    )
    service = make_upgrade_service(tmp_path, queries)

    from server.app.services import job_workflow_upgrade_apply as upgrade_module
    from server.app.services import job_workflow_upgrade_skill_heads as heads

    real_plan = upgrade_module.plan_inherit_nodes
    real_rev = heads._rev_parse_head
    rev_calls: list[str] = []

    def counting_rev(repo: Path) -> str | None:
        rev_calls.append(str(repo))
        return real_rev(repo)

    monkeypatch.setattr(heads, "_rev_parse_head", counting_rev)

    def plan_then_advance(job_db, job, new_definition, frozen_json, **kwargs):
        inherit = real_plan(job_db, job, new_definition, frozen_json, **kwargs)
        assert "b" in inherit  # plan 时 HEAD == 执行记录，b 可继承
        _advance_head(repo)  # 判定后 HEAD 前进（残余窗口）
        return inherit

    monkeypatch.setattr(upgrade_module, "plan_inherit_nodes", plan_then_advance)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # guard 重验不重新 rev-parse（沿用 plan 常量）→ b 仍继承。
    assert result["kept_node_count"] == 2
    assert _statuses(queries, job_id) == {"a": "completed", "b": "completed", "c": "pending"}
    # rev-parse 仅 plan 阶段一次；guard 事务内零 git 子进程。
    assert len(rev_calls) == 1


# ---------------------------------------------------------------------------
# 评审 P2-1：skill key 字符集白名单（NUL 等 OS 级非法字符防 500）
# ---------------------------------------------------------------------------


def test_skill_repo_dir_rejects_unsafe_skill_keys() -> None:
    """``_skill_repo_dir`` 白名单：合法两段 key → 路径；其余形态 → None。

    NUL 字节穿字符串结构校验（两段/非空/无 ``..``/非绝对）但在 subprocess
    参数编码处抛 ValueError——白名单先行拒绝（评审 P2-1 实测复现形态）。
    空段/绝对前缀/``.``/``..``/三段/unicode 均不匹配 ``^[A-Za-z0-9][A-Za-z0-
    9._-]*$``，旧结构校验被白名单完整覆盖（P3-3：路径逃逸形态构造不出来，
    无需 resolve/containment）。
    """
    from server.app.services.job_workflow_upgrade_skill_heads import _skill_repo_dir

    root = Path("/tmp/skills")
    assert _skill_repo_dir(root, "g/n") == root / "g" / "n"
    for bad in (
        "a/\x00b",  # P2-1：NUL（POSIX subprocess 参数编码抛 ValueError）
        "g/名",  # 非 ASCII
        "g/with space",
        "g/..",  # 旧校验显式拒绝的形态，白名单同样拒绝
        "/abs/x",  # 绝对前缀 → 首段为空
        "a/",
        "a",
        "g/n/s",  # 三段
        "./n",  # ``.`` 段
    ):
        assert _skill_repo_dir(root, bad) is None, bad


def test_rev_parse_head_nul_path_returns_none() -> None:
    """双保险（P2-1）：``_rev_parse_head`` 对含 NUL 的路径返回 None 不抛。

    即使上游白名单被绕过，subprocess 参数编码的 ValueError 也被 except
    元组兜住（OSError/ValueError/SubprocessError）。
    """
    from server.app.services.job_workflow_upgrade_skill_heads import _rev_parse_head

    assert _rev_parse_head(Path("/nonexistent/a\x00b")) is None


def test_latest_nul_skill_key_conservative_rerun_no_crash(tmp_path: Path) -> None:
    """含 NUL 的 skill key（节点绑定）→ 保守排除、不 500（P2-1 回归）。

    旧缺陷：``_skill_repo_dir`` 结构校验放行 → ``subprocess.run`` 参数编码
    抛 ValueError → 穿出 ``resolve_latest_skill_heads`` → 单 job 升级路由
    500（main 上同数据态走「latest 恒排除」优雅降级，失败语义回归）。
    """
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY + "\x00", ref="latest"),
        skill_version=f"latest@{'1' * 12}",
        skill_commit="1" * 40,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 仓库不可解析 → None → 保守重跑，升级本身成功（不 500）。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert _statuses(queries, job_id) == {"a": "completed", "b": "pending", "c": "pending"}


# ---------------------------------------------------------------------------
# 评审 P3-5：交叉面与调用次数钉子
# ---------------------------------------------------------------------------


def test_latest_head_matches_but_impl_identity_drift_reruns(tmp_path, monkeypatch) -> None:
    """proven ∩ 实现面排除交集：latest HEAD 匹配（skill 面已证明）但实现
    身份漂移 → 仍重跑。

    skill 证明不掩盖 P1-1：Agent 定义重发布（config_schema 变化 →
    definition_hash 漂移）时，即使执行记录 commit == 当前 HEAD，b 也不可
    继承——``latest_proven`` 派生时已扣除实现面排除集，S5 与 P1-A 同向。
    """
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{head[:12]}",
        skill_commit=head,
    )
    # skill HEAD 匹配执行记录，但 Agent 定义重发布 → 实现身份漂移。
    from server.app.agent_catalog import AgentDefinition
    from tests.helpers import replace_agent_catalog

    v2 = AgentDefinition(
        capability="cap_b",
        runtime="pi",
        config_schema={"type": "object", "properties": {"k": {"type": "string"}}},
    )
    replace_agent_catalog(workspace["id"], {"agent-b": v2})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 无实现漂移时同构场景 b 继承（见 head_unchanged 用例）；此处 b 重跑。
    assert result["kept_node_count"] == 1
    assert _statuses(queries, job_id) == {"a": "completed", "b": "pending", "c": "pending"}


def _shared_key_two_nodes_job(tmp_path: Path, head: str):
    """a(code) → b(agent) → c(code) + d(agent, 与 b 同 skill key) 的链。

    b/d 显式绑定同一 ``SKILL_KEY`` latest——钉 per-key rev-parse 的判定
    涉及面（同 key 多节点只解析一次）。
    """
    import dataclasses

    from server.app.agent_catalog import AgentDefinition
    from tests.helpers import replace_agent_catalog
    from tests.helpers.job_workflow_upgrade import (
        publish_node_code,
        seed_done_execution,
        seed_reachable_outputs,
        seed_wfchain_job,
        setup_wfchain_env,
        wfchain_agent_definition,
    )

    definition = wfchain_agent_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(
        nodes["b"], skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest")
    )
    nodes["d"] = WorkflowNode(
        key="d",
        label="D",
        capability="cap_d",
        after=["a"],
        node_type="agent",
        skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
    )
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    agent_b = AgentDefinition(capability="cap_b", runtime="pi")
    agent_d = AgentDefinition(capability="cap_d", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": agent_b, "agent-d": agent_d})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c", "d"])
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "d"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    for key, agent in (("b", agent_b), ("d", agent_d)):
        seed_done_execution(
            queries,
            workspace["id"],
            job_id,
            key,
            kind="agent",
            impl_hash=agent.definition_hash(),
            skill=SKILL_KEY,
            skill_version=f"latest@{head[:12]}",
            skill_commit=head,
        )
    queries.update_job_status(job_id, "completed")
    seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    return queries, workspace, job_id


def test_latest_shared_skill_key_resolved_once_per_key(tmp_path, monkeypatch) -> None:
    """per-key 解析：同 key 的多个 latest 节点共享一次 rev-parse。

    ``resolve_latest_skill_heads`` 按 skill key 去重后解析——b/d 同绑
    ``SKILL_KEY`` → 恰一次 git 调用；两节点都因 HEAD 匹配而继承。
    """
    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = _shared_key_two_nodes_job(tmp_path, head)
    service = make_upgrade_service(tmp_path, queries)

    from server.app.services import job_workflow_upgrade_skill_heads as heads

    real_rev = heads._rev_parse_head
    rev_calls: list[str] = []

    def counting_rev(repo_path: Path) -> str | None:
        rev_calls.append(str(repo_path))
        return real_rev(repo_path)

    monkeypatch.setattr(heads, "_rev_parse_head", counting_rev)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 同 key 一次解析（两个 latest 节点共享）；a/b/d 继承，c 无记录重跑。
    assert rev_calls == [str(repo)]
    assert result["kept_node_count"] == 3
    assert _statuses(queries, job_id) == {
        "a": "completed",
        "b": "completed",
        "c": "pending",
        "d": "completed",
    }


def test_revision_change_retry_re_resolves_latest_heads(tmp_path, monkeypatch) -> None:
    """``ActiveRevisionChangedError`` 重试后重新 resolve（不带旧常量）。

    首次尝试 resolve 得 H1 → plan → 竞争窗口发布新 revision → guard 重读
    不符 → 整体重试。第二次尝试必须重新 ``resolve_latest_skill_heads``
    （#759 4.4：重解 context + 重 plan，全部输入来自新 context）——若沿
    用首次的旧 HEAD 常量，plan 会用过期值判定。本用例在第二次 resolve
    前推进仓库 HEAD：重试消费新 HEAD（≠ 执行记录）→ b 保守重跑；携带旧
    常量的缺陷形态则会让 b 继续继承（kept=2）。
    """
    import json

    from server.app.services import job_workflow_upgrade_apply as upgrade_module
    from server.app.services import job_workflow_upgrade_skill_heads as heads
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.definition import workflow_definition_from_dict

    base_dir = tmp_path / "skills"
    repo = _make_skill_repo(base_dir)
    head = _head(repo)
    _patch_skill_root(monkeypatch, base_dir)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{head[:12]}",
        skill_commit=head,
    )
    service = make_upgrade_service(tmp_path, queries)

    real_plan = upgrade_module.plan_inherit_nodes
    real_resolve = heads.resolve_latest_skill_heads
    resolved_heads: list[str | None] = []

    def resolve_and_track(job_db, job, definition):
        if resolved_heads:
            # 第二次尝试：resolve 前推进 HEAD（模拟两次尝试间的仓库前进）。
            _advance_head(repo)
        result = real_resolve(job_db, job, definition)
        resolved_heads.append(result.commits.get(SKILL_KEY))
        return result

    monkeypatch.setattr(upgrade_module, "resolve_latest_skill_heads", resolve_and_track)

    def plan_then_publish(job_db, job, new_definition, frozen_json, **kwargs):
        inherit = real_plan(job_db, job, new_definition, frozen_json, **kwargs)
        if len(resolved_heads) == 1:
            # 竞争窗口（首尝）：plan 之后、guard 事务前 active revision 被重
            # 发布（同内容新版本）→ guard 重读 id 不符 → ActiveRevisionChangedError。
            revisions = WorkflowRevisionService(job_db)
            same = workflow_definition_from_dict(
                json.loads(str(job["workflow_definition_snapshot_json"]))
            )
            revisions.publish_workspace_revision(str(job["workspace_id"]), same)
        return inherit

    monkeypatch.setattr(upgrade_module, "plan_inherit_nodes", plan_then_publish)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 两次尝试各 resolve 一次，第二次读到推进后的新 HEAD。
    assert len(resolved_heads) == 2
    assert resolved_heads[0] == head
    assert resolved_heads[1] != head
    # 重试消费新 HEAD（≠ 执行记录 H1）→ b 保守重跑；不带旧常量的判别点。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert _statuses(queries, job_id) == {"a": "completed", "b": "pending", "c": "pending"}


# ---------------------------------------------------------------------------
# #1166 P2：整批 rev-parse 的批级总预算（预算矩阵：快路径 / 混合 / 慢存储）
# ---------------------------------------------------------------------------


def _budget_constants(monkeypatch, *, budget: float, timeout: float):
    """注入预算模型常数（测试沙箱）；返回模块引用供 ``_rev_parse_head`` 替换。"""
    from server.app.services import job_workflow_upgrade_skill_heads as heads

    monkeypatch.setattr(heads, "_REV_PARSE_TIMEOUT_SECONDS", timeout)
    monkeypatch.setattr(heads, "_REV_PARSE_BUDGET_SECONDS", budget)
    return heads


def _latest_nodes_definition(key_count: int) -> WorkflowDefinition:
    """``key_count`` 个 latest 绑定 agent 节点的最小定义（预算矩阵公共构造）。"""
    return WorkflowDefinition(
        key="wfbudget",
        label="WF",
        intake=WorkflowIntake(),
        nodes={
            f"n{index}": WorkflowNode(
                key=f"n{index}",
                label=f"N{index}",
                capability=f"cap{index}",
                node_type="agent",
                skill=WorkflowNodeSkill(key=f"g/sk{index}", ref="latest"),
                execution=WorkflowNodeExecution(runtime="pi"),
            )
            for index in range(key_count)
        },
    )


def _resolve_for_budget_matrix(heads, tmp_path: Path, definition, ws_name: str):
    """独立 workspace 跑一次 resolve（预算矩阵公共入口）。"""
    from server.app.jobs import JobQueries
    from tests.postgres_support import TEST_DATABASE_URL

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(ws_name)
    return heads.resolve_latest_skill_heads(
        queries, {"workspace_id": workspace["id"]}, definition, base_dir=tmp_path / "skills"
    )


class _FakeClock:
    """#1166 评审 P3-2：确定性门判定——预算循环只读 ``time.monotonic``
    （门判定与 ``_rev_parse_head`` 内的 sleep 解耦），注入假时钟让每次
    读数前进固定步长：门的开/关不再依赖真实墙钟，xdist 负载下零抖动。
    被测代码引用 ``heads.time.monotonic``（模块属性），monkeypatch 替换。
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.step = 0.0

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


def test_rev_parse_batch_budget_single_fast_key_unaffected(tmp_path, monkeypatch) -> None:
    """预算矩阵 {1 快 key}：预算层对快路径零影响——真实常数（30s 批预算 /
    5s 单次超时）下单 key 即时 rev-parse 正常解析（非 None、恰好一次
    调用、结果 commit 正确）。启动门只拦截「启动前剩余预算不足」，
    启动后的快调用不会被截断。"""
    from server.app.services import job_workflow_upgrade_skill_heads as heads

    rev_calls: list[str] = []

    def fast_rev(repo: Path) -> str | None:
        rev_calls.append(str(repo))
        return "a" * 40

    monkeypatch.setattr(heads, "_rev_parse_head", fast_rev)
    clock = _FakeClock()
    monkeypatch.setattr(heads.time, "monotonic", clock.monotonic)

    resolved = _resolve_for_budget_matrix(
        heads, tmp_path, _latest_nodes_definition(1), "wsbudget-fast"
    )

    assert resolved.commits == {"g/sk0": "a" * 40}
    assert len(rev_calls) == 1  # 无预算损耗：key 启动且只解析一次


def test_rev_parse_batch_budget_mixed_fast_and_slow_keys(tmp_path, monkeypatch) -> None:
    """预算矩阵 {混合快+慢} 两段（假时钟确定性形态，步长 = 每次门读数推
    进量——「慢 key」即大步长）：

    - 预算未耗尽段：慢 key 消耗预算后，其后的快 key 仍正确解析（快 key
      不被慢邻居连坐——启动门只要剩余预算 ≥ 单次超时就放行）；
    - 预算耗尽段：慢 key 吃光预算后，快 key 也保守 None 不启动——启动门
      按「剩余预算」判定，与 key 自身快慢无关（快不是通行证）。

    常数：budget=2.0 / timeout=0.25；段一慢步长 0.5（第 4 门剩余
    2.0-1.5=0.5 ≥ 0.25，放行——门分离 0.25）；段二 budget=0.6、慢步长
    0.5（第 2 门剩余 0.1 < 0.25，关门——门分离 0.15）。"""

    def _run(budget: float, slow: float, key_count: int, ws_name: str, calls: list[str]):
        heads = _budget_constants(monkeypatch, budget=budget, timeout=0.25)
        clock = _FakeClock()

        def fake_rev(repo: Path) -> str | None:
            calls.append(str(repo))
            clock.step = slow  # 慢 key：rev-parse 期间时钟走 slow
            return "b" * 40

        monkeypatch.setattr(heads, "_rev_parse_head", fake_rev)
        monkeypatch.setattr(heads.time, "monotonic", clock.monotonic)
        return _resolve_for_budget_matrix(
            heads, tmp_path, _latest_nodes_definition(key_count), ws_name
        )

    # 段一：4 个慢 key（步长 0.5），预算 2.0 → 门序列 1.5/1.0/0.5 ≥ 0.25
    # 全放行，第 5 门 0.0 < 0.25 关（但只有 4 个 key）→ 4 key 全解析。
    phase_a_calls: list[str] = []
    resolved_a = _run(2.0, 0.5, 4, "wsbudget-mix-a", phase_a_calls)
    assert resolved_a.commits == {f"g/sk{index}": "b" * 40 for index in range(4)}
    assert len(phase_a_calls) == 4  # 慢 key 之后快 key 仍启动并解析

    # 段二：慢 key 吃光预算（budget 0.6 - 步长 0.5 = 剩 0.1 < 0.25）→
    # 其后的快 key 保守 None（不启动、零 git 调用）。
    phase_b_calls: list[str] = []
    resolved_b = _run(0.6, 0.5, 2, "wsbudget-mix-b", phase_b_calls)
    assert resolved_b.commits == {"g/sk0": "b" * 40, "g/sk1": None}
    assert len(phase_b_calls) == 1  # 快 key 未启动：门按剩余预算，不看快慢


def test_rev_parse_batch_budget_bounds_total_and_skips_rest(tmp_path, monkeypatch) -> None:
    """#1166 P2：串行循环的单次 5s 超时只保证局部有界——N 个 key 在慢存储
    （NFS 挂起）形态理论最坏 N×单次超时，管理请求会先被 HTTP 超时杀掉而
    不是走 None 保守降级。慢 key（大时钟步长）逐 key 消耗批预算，断言
    批级 deadline 的三个面：预算内前缀正常解析、剩余预算不足单次超时的
    后续 key 保守 None（不再启动 git）、未启动 key 零 git 调用。

    #1166 评审 P3-2：门判定改假时钟（``_FakeClock`` 确定性步长）——原
    真实 sleep 形态的门分离只有 0.15s、elapsed 断言余量 0.55s，xdist
    8 worker 共享单 PG 下可预见 flake；假时钟下门开/关与墙钟完全解耦。
    常数：budget=1.5 / timeout=0.45 / 慢步长 0.45——第 1 门（读取时
    时钟零步进、剩余 1.5）放行、rev-parse 内步进 0.45；门序列
    1.05 / 0.60 ≥ 0.45 放行（门分离 0.15——假时钟下确定），第 4 门
    0.15 < 0.45 永不可达。
    """
    heads = _budget_constants(monkeypatch, budget=1.5, timeout=0.45)
    key_count = 10
    rev_calls: list[str] = []
    clock = _FakeClock()

    def slow_rev(repo: Path) -> str | None:
        rev_calls.append(str(repo))
        clock.step = 0.45  # 每个 rev-parse 期间时钟走满单次超时
        return "3" * 40

    monkeypatch.setattr(heads, "_rev_parse_head", slow_rev)
    monkeypatch.setattr(heads.time, "monotonic", clock.monotonic)

    resolved_heads = _resolve_for_budget_matrix(
        heads, tmp_path, _latest_nodes_definition(key_count), "wsbudget"
    )

    sorted_keys = sorted(f"g/sk{index}" for index in range(key_count))
    assert list(resolved_heads.commits) == sorted_keys  # 全 key 有槽位（含 None）
    commits = [resolved_heads.commits[key] for key in sorted_keys]
    # 预算内前缀 3 个 key 实际解析；预算耗尽后 7 个 key 保守 None。
    assert commits == ["3" * 40] * 3 + [None] * 7
    # 未启动的 key 不产生 git 调用（启动次数 = 非 None 数，封顶 floor(预算/超时)）。
    assert len(rev_calls) == 3
    assert resolved_heads.bound_nodes == frozenset(f"n{index}" for index in range(key_count))
