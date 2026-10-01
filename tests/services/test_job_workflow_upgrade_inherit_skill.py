"""inherit 升级的 skill 内容身份判定测试（issue #645 codex 五轮 P1-A，#759 收紧）。

三态判定：latest（节点显式 / 空归一 / AgentDefinition legacy 兜底）恒定排除、
pinned ref 与 DB 锁文档直读值比较、锁内无条目即排除；upgrade 全链路零 git
I/O、永不触发首次 pin。自 ``test_job_workflow_upgrade_inherit_codex5.py``
按主题拆出（零改动迁移）。
"""

from pathlib import Path

from server.app.workflows.schema import (
    WorkflowNodeSkill,
)
from tests.helpers.job_workflow_upgrade import (
    COMMIT_V1,
    COMMIT_V2,
    SKILL_KEY,
    make_upgrade_service,
    no_git_spy,
    put_skill_lock,
    skill_bound_job,
)

# ---------------------------------------------------------------------------
# P1-A：skill 内容身份三态判定（#759 收紧：latest 恒排除 / pinned 锁比对 /
# 无锁条目排除；upgrade 零 git I/O、永不 pin）
# ---------------------------------------------------------------------------


def test_skill_legacy_fallback_latest_binding_always_reruns_node(
    tmp_path: Path, monkeypatch
) -> None:
    """#759 P1 语义反转：legacy fallback（ref 恒 latest）恒定排除，零 git I/O。

    旧语义（本用例前身 ``test_skill_legacy_fallback_matching_commit_keeps_node``
    的反转）：执行记录 commit 与 live HEAD 解析相等即可继承——但 upgrade
    判定之后 HEAD 仍可前进，commit 对比证明不了继承安全性，且判定本身要跑
    git 子进程（rev-parse HEAD）。新语义：latest 绑定（含空 ref 归一与
    AgentDefinition legacy 兜底）不做任何 git 解析，直接排除。
    """
    git_calls = no_git_spy(monkeypatch)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        agent_skill=SKILL_KEY,
        skill_version=f"latest@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 的 latest 绑定恒定排除 → b 及下游 c 重跑；a 无 skill 面 → 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    assert git_calls == []


def test_skill_explicit_latest_ref_always_reruns_node(tmp_path: Path, monkeypatch) -> None:
    """节点显式 ``skill: latest``：S5 与 P1-A skill 面同向排除，零 git I/O。"""
    git_calls = no_git_spy(monkeypatch)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        skill_version=f"latest@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    assert git_calls == []


def test_skill_pinned_matching_lock_commit_keeps_node(tmp_path: Path, monkeypatch) -> None:
    """pinned 正面对照：锁内 refs[ref] 与执行记录 commit 相等 → 照常继承。

    证明 pinned 面的判别点是锁内 commit 比较本身，而非「带 skill 的 agent
    节点一律排除」的粗面。
    """
    git_calls = no_git_spy(monkeypatch)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V1}}})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 锁内 v1 == 执行记录 commit → a/b 继承；c 无执行记录 → 保守重跑。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}
    assert git_calls == []


def test_skill_pinned_matching_lock_prefix_record_keeps_node(tmp_path: Path) -> None:
    """pinned 前缀形态：记录只有 node_runs 的 ``ref@commit12`` 时按前缀等长截断比较。"""
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit="",  # 请求行 manifest 无完整 sha → 回落 version 前缀
    )
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V1}}})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}


def test_skill_pinned_relock_drift_reruns_node(tmp_path: Path) -> None:
    """pinned 漂移面：make skills-lock 重解析后锁内 commit ≠ 执行记录 → 重跑。

    节点定义与 Agent hash 都不变，但 dispatch 经锁解析出的 commit 已漂移
    （旧 ``test_skill_pinned_tag_relock_drift_reruns_node`` 的 DB 锁形态）。
    """
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V2}}})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 锁内 v1=C2 ≠ 执行记录 C1 → b 及下游 c 重跑；a 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_skill_pinned_ref_missing_from_lock_reruns_node(tmp_path: Path) -> None:
    """锁内无该 ref → 不可证明 → 排除（upgrade 永不触发首次 pin）。

    pin 写只属于 dispatch 热路径与 ``make skills-lock``；upgrade 看到
    未 pin 的 ref 只能保守重跑。
    """
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    # 锁文档存在、该 skill 有条目，但 refs 里没有 v1。
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v2": COMMIT_V2}}})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_skill_pinned_without_lock_document_reruns_node(tmp_path: Path) -> None:
    """锁文档整体缺失（从未播种）→ pinned 绑定同样不可证明 → 排除。"""
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}


def test_skill_relock_between_plan_and_guard_uses_fresh_lock(tmp_path: Path, monkeypatch) -> None:
    """#759 P1 跨进程 relock 交错：guard 重验必须读到 DB 最新锁文档。

    旧缺陷：pinned 判定经 ``SkillManager._doc_cache``（5s TTL）——plan 读
    {v1→C1} 后另一进程 ``put_lock`` 改写为 {v1→C2}，5s 窗口内 guard 事务
    内重验读到的仍是 C1（stale），已漂移节点被继承。修复后 plan 与重验
    都直读 DB（绕开 doc cache）：plan 消费 C1 得出继承集，重验读到 C2 →
    b 放弃继承。同时钉住 guard 重验路径零 git I/O。
    """
    git_calls = no_git_spy(monkeypatch)
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version=f"v1@{COMMIT_V1[:12]}",
        skill_commit=COMMIT_V1,
    )
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V1}}})
    service = make_upgrade_service(tmp_path, queries)

    from server.app.services import job_workflow_upgrade_apply as upgrade_module

    real_plan = upgrade_module.plan_inherit_nodes

    def plan_then_relock(job_db, job, new_definition, frozen_json, **kwargs):
        inherit = real_plan(job_db, job, new_definition, frozen_json, **kwargs)
        assert "b" in inherit  # plan 时锁内 v1=C1，与执行记录一致
        # 另一「进程」（make skills-lock）relock：v1 → C2（直写 DB，绕过
        # 任何本进程 doc cache）。
        put_skill_lock(job_db, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V2}}})
        return inherit

    monkeypatch.setattr(upgrade_module, "plan_inherit_nodes", plan_then_relock)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # guard 重验读到 C2 ≠ 执行记录 C1 → b 放弃继承（降级重跑，下游 c 跟随）。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    assert git_calls == []


def test_skill_no_execution_record_excludes_agent_node(tmp_path: Path) -> None:
    """P1-A 不可证明面：pinned 绑定但执行记录无 skill commit → 保守重跑。

    v75 前的 node_runs 无 skill_version、请求行 manifest 也无 skill 键时
    （数据态或旧执行），旧产物按哪份 skill 内容产出不可知——即使锁内
    有该 ref 也无法证明一致。
    """
    queries, workspace, job_id = skill_bound_job(
        tmp_path,
        node_skill=WorkflowNodeSkill(key=SKILL_KEY, ref="v1"),
        skill_version="",
        skill_commit="",
    )
    put_skill_lock(queries, {SKILL_KEY: {"repo": "", "refs": {"v1": COMMIT_V1}}})
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b 的 skill commit 不可证明 → 重跑（b 及下游 c）；a 继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
