"""inherit 升级的 guard 事务内重验与退化 clean 测试（codex 五轮 P2-C/P2-D）。

P2-C：plan→mutation 的 TOCTOU——guard 事务内重验实现身份（纯 DB 读 +
字符串比较），漂移节点放弃继承降级重跑；P2-D：无旧快照作业（退化 clean）
清理全部旧清单行。自 ``test_job_workflow_upgrade_inherit_codex5.py`` 按主题
拆出（零改动迁移）。
"""

import dataclasses
from contextlib import closing
from pathlib import Path

from server.app.agent_catalog import AgentDefinition
from server.app.db.connection import connect_database
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    no_git_spy,
    publish_node_code,
    seed_done_execution,
    seed_reachable_outputs,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_agent_definition,
    wfchain_definition,
)

# ---------------------------------------------------------------------------
# P2-C：guard 事务内重验实现身份（plan → mutation TOCTOU）
# ---------------------------------------------------------------------------


def test_guard_revalidates_agent_identity_republished_after_plan(
    tmp_path: Path, monkeypatch
) -> None:
    """codex 五轮 P2-C：plan 之后、guard 事务前 Agent 重发布 → 降级重跑。

    旧缺陷（``_published_catalog`` 注释自认）：继承集在事务外规划，
    ``resolve_upgrade_context`` 与 ``lease_guarded_mutation`` 之间 Agent
    定义被重新发布时，guard 只查 lease/running 不验 published 身份——
    事务消费旧继承集，旧实现产物冒充新实现。修复：在受序列化保护的
    应用阶段（guard 事务内）重验实现身份，漂移节点放弃继承（降级重跑，
    与事务内收敛层的 keep ∩ completed + shared_name 复算同一防线风格）。
    #759 P1：重验路径同时钉住零 git I/O（skill 面直读 DB 锁文档）。
    """
    git_calls = no_git_spy(monkeypatch)
    definition = wfchain_agent_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    c_hash = publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    seed_done_execution(queries, workspace["id"], job_id, "c", kind="code", impl_hash=c_hash)
    queries.update_job_status(job_id, "completed")
    job_dir = seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = make_upgrade_service(tmp_path, queries)

    # 竞争窗口：plan_inherit_nodes 内（返回前）重发布 Agent 定义——
    # plan 消费 V1 catalog 得到继承集 {a, b, c}，guard 事务看到的已是 V2。
    from server.app.services import job_workflow_upgrade_apply as upgrade_module

    real_plan = upgrade_module.plan_inherit_nodes
    real_revalidate = upgrade_module.implementation_excluded_nodes
    real_lock = queries.acquire_implementation_publication_lock
    lock_held: list[bool] = []

    def acquire_lock(conn, workspace_id):
        real_lock(conn, workspace_id)
        lock_held.append(True)

    def revalidate(*args, **kwargs):
        assert lock_held == [True]
        return real_revalidate(*args, **kwargs)

    monkeypatch.setattr(queries, "acquire_implementation_publication_lock", acquire_lock)
    monkeypatch.setattr(upgrade_module, "implementation_excluded_nodes", revalidate)

    def plan_then_republish(job_db, job, new_definition, frozen_json, **kwargs):
        inherit = real_plan(job_db, job, new_definition, frozen_json, **kwargs)
        assert {"b", "c"} <= inherit  # plan 时 V1 与下游身份都匹配
        # 不携带 skill：latest 绑定恒定排除（P1-A）会掩盖本用例的哈希
        # 漂移判别点。
        v2 = AgentDefinition(
            capability="cap_b",
            runtime="pi",
            config_schema={"type": "object", "properties": {"k": {"type": "string"}}},
        )
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
    assert git_calls == []


def test_guard_revalidation_no_drift_keeps_planned_inherit(tmp_path: Path) -> None:
    """P2-C 对照组：无 TOCTOU 漂移时重验零影响（继承集不被无谓收缩）。"""
    definition = wfchain_agent_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    for key in ("a", "b"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 重验与 plan 同源同时刻（无漂移）→ a/b 照常继承；c 无记录保守重跑。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}


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
    definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
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
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    seed_reachable_outputs(queries, job_id, ["a_out.json", "b_out.json"])
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
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 退化 clean：全量重跑（kept=0）；全部旧清单行（含旧 key 的孤儿行）
    # 同事务删除——产物 API 不再展示旧 revision 产物。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b", "c", "old"})
    assert names == set()
