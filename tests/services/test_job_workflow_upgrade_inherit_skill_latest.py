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

from server.app.workflows.schema import WorkflowNode, WorkflowNodeSkill
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
