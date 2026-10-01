"""inherit 升级的 frozen 配置基准测试（issue #645，A1/codex #776 P2）。

旧侧配置基准只用 job 存量 ``frozen_config_json``：漂移即重跑、不变即
继承；NULL（legacy 作业）恒退化全量重跑——生产时配置不可证明。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.workflows.schema import WorkflowDefinition
from tests.helpers.job_workflow_upgrade import (
    inherit_chain_definition,
    seed_impl_identity,
    seed_inherit_job,
    setup_inherit_env,
)


def _config_schema_chain_definition() -> WorkflowDefinition:
    """a 带 config_schema 的三级链（P1-2 用例的公共构造）。"""
    import dataclasses

    base = inherit_chain_definition()
    schema = {
        "type": "object",
        "properties": {"bank_version": {"type": "string", "default": "v5"}},
    }
    nodes = dict(base.nodes)
    nodes["a"] = dataclasses.replace(base.nodes["a"], config_schema=schema)
    return dataclasses.replace(base, nodes=nodes)


def test_inherit_upgrade_resets_nodes_on_frozen_config_drift(tmp_path: Path) -> None:
    # review P1-2：intake 后 workspace override 变化 → 新侧 re-freeze 与
    # job 存量 frozen_config_json 的差异即「配置演进」，受影响节点重跑。
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    # intake 时冻结的配置：bank_version=v5。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({"a": {"bank_version": "v5"}}, sort_keys=True), job["id"]),
        )
    # intake 之后 workspace override 改了 bank_version。
    queries.update_workspace(
        workspace["id"],
        node_config={"wfchain": {"a": {"bank_version": "v6"}}},
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 按旧配置产出产物、新配置是 v6 → a 及下游 b/c 重跑，不继承。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_revision_id"] == current["id"]
    assert json.loads(upgraded["frozen_config_json"])["a"]["bank_version"] == "v6"


def test_inherit_upgrade_keeps_nodes_when_frozen_config_unchanged(tmp_path: Path) -> None:
    # P1-2 配对用例：workspace override 与 intake 冻结值一致 → 配置无演进，
    # 未变节点照常继承（防止把「优先旧冻结值」做成「永远全量重跑」）。
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    revisions.publish_workspace_revision(workspace["id"], _config_schema_chain_definition())
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    # workspace override 与 intake 冻结值相同（都解析为 v5）；job 的存量
    # 冻结值按 intake 的完整形状播种（含平台保留执行键与全部节点段）。
    queries.update_workspace(
        workspace["id"],
        node_config={"wfchain": {"a": {"bank_version": "v5"}}},
    )
    from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json

    intake_frozen = intake_frozen_config_json(
        queries, workspace["id"], _config_schema_chain_definition()
    )
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (intake_frozen, job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert result["kept_node_count"] == 3
    assert statuses == {"a": "completed", "b": "completed", "c": "completed"}


def test_inherit_upgrade_null_frozen_degrades_to_full_rerun(tmp_path: Path) -> None:
    """A1（对抗审查）：legacy NULL-frozen 作业的旧侧配置基准不可证明。

    RUN-FREEZE-001 之前的存量作业 dispatch 走现场解析，其产物基准是
    **生产时刻**的 workspace 配置。若旧侧回退到「在旧定义上按今天的
    配置 re-freeze」，配置演进（生产后新增的 override）会被两侧同源
    re-freeze 吸收（新旧恒等）→ 旧配置产物冒充新 revision 产物被继承。
    正确语义：无法证明旧 config 基准 → 保守退化到全量重跑（与损坏
    快照 JSON 的降级方向一致）。对照：frozen 非 NULL 的同款演进由
    ``test_inherit_upgrade_resets_nodes_on_frozen_config_drift`` 钉住。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    schema = {
        "type": "object",
        "properties": {"bank_version": {"type": "string"}},
    }
    base = inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(base.nodes["a"], config_schema=schema, outputs=["a_out.json"]),
        "b": dataclasses.replace(base.nodes["b"], inputs=["a_out.json"], outputs=["b_out.json"]),
        "c": base.nodes["c"],
    }
    definition = dataclasses.replace(base, nodes=nodes)
    queries, workspace, revisions, _, service = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    # 产物在「无 override」时期产出（legacy 作业当时 live 解析，无冻结值）。
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text('{"bank_version": "legacy-default"}')
    # 模拟 legacy 存量行：intake 冻结值被置 NULL。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute("update jobs set frozen_config_json=null where id=%s", (job["id"],))
    # 生产之后 workspace 配置演进（新增 override v9）。
    queries.update_workspace(
        workspace["id"],
        node_config={"wfchain": {"a": {"bank_version": "v9"}}},
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # 旧侧基准不可证明 → 全量重跑：legacy-default 产物不再冒充 v9 产物。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_revision_id"] == current["id"]
    assert json.loads(upgraded["frozen_config_json"])["a"]["bank_version"] == "v9"


def test_inherit_upgrade_null_frozen_without_intake_evidence_degrades(
    tmp_path: Path, monkeypatch
) -> None:
    """codex #776 复审 P2：NULL frozen = legacy 作业，生产时配置不可证明 → 退化。

    legacy 作业 dispatch 走现场解析（run_payload 的 frozen=None 臂），生产时
    的 workspace override 可能已删除；按**当前** override 重算旧定义得到空
    配置不构成「旧侧为空」的证据。修复：NULL 恒退化 clean，不再探测当前
    配置面。monkeypatch 把 re-freeze 压成空（模拟当前配置面为空的历史
    场景），钉住「NULL 即退化」不再因探针为空而放行继承。
    """
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    # 强制 legacy 形态：frozen_config_json IS NULL（覆盖 _inherit_job 的播种）。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute("update jobs set frozen_config_json=null where id=%s", (job["id"],))
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")

    import server.app.services.job_workflow_upgrade_gates as gates_module
    import server.app.services.job_workflow_upgrade_plan as plan_module

    # 修复前 plan 的探针决定 NULL 是否放行：把 gates/plan 两处 re-freeze 都
    # 压成 None（模拟当前配置面为空的历史场景——新侧冻结也为空，S2 比较
    # 退化为 {} == {}），旧代码在此放行继承 → 本用例红；修复后 NULL 恒
    # 退化、plan 探针已删除（raising=False 惰性化），用例钉住终态语义。
    _empty_refreeze = lambda *args, **kwargs: None  # noqa: E731
    monkeypatch.setattr(gates_module, "intake_frozen_config_json", _empty_refreeze)
    monkeypatch.setattr(plan_module, "intake_frozen_config_json", _empty_refreeze, raising=False)

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # NULL + 当前配置面为空也不许继承：全量重跑。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
