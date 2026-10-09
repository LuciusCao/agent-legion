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

from server.app.workflows.schema import WorkflowNodeSkill
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
