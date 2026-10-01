"""inherit 升级的 runtime_mutable 键排除测试（issue #645 codex 四轮 P1-3）。

节点自声明 / Agent 定义侧 runtime_mutable 键 → 恒重跑（每次 dispatch 重解析，
继承无意义）；HIGH-2 复审：定义不变只翻转 override 的组合覆盖。自
``test_job_workflow_upgrade_inherit_codex4.py`` 按主题拆出（零改动迁移）。
"""

from pathlib import Path

from server.app.agent_catalog import AgentDefinition
from server.app.workflows.schema import WorkflowDefinition, WorkflowNode
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    publish_node_code,
    seed_done_execution,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_definition,
)

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
    definition = wfchain_definition({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], config_schema=schema)
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    # 同 revision 内容再发布（定义零变更）——排除规则与 diff 变更无关。
    revisions.publish_workspace_revision(workspace["id"], definition)
    # a/b 的实现身份均可证明（隔离 P1-1 保守面，让判别点收敛在 P1-3 上：
    # 去掉 runtime_mutable 排除时 b 本应被继承）。
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    queries.update_job_status(job_id, "completed")
    # b 的产物可达（本地文件在位）——隔离不可达退化：去掉 P1-3 排除时
    # b 本应被继承，判别点收敛在 runtime_mutable 排除上。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-with-dry-run-B")
    service = make_upgrade_service(tmp_path, queries)

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

    definition = wfchain_definition()
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
    definition = wfchain_definition({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent", capability="cap_b")
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    # 同 revision 内容再发布（定义零变更）——排除规则与 diff 变更无关。
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    # Agent 定义带 runtime_mutable 键（b 的节点级 config_schema 为空——
    # 旧缺陷：有效 schema 主体在 Agent 定义里，节点自声明判定覆盖不到）。
    # 不携带 skill：latest 绑定在 #759 后恒定排除（codex5 P1-A 面），会
    # 掩盖本用例的 HIGH-2 判别点。
    v1 = AgentDefinition(capability="cap_b", runtime="pi", config_schema=schema)
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # b 的实现身份可证明且未漂移（定义不变、执行时哈希 == 当前哈希）——
    # 隔离 P1-1 保守面，让判别点收敛在 HIGH-2 上：去掉定义侧排除时 b
    # 本应被继承。a 走 code 路径，同样可证明。
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    # a/b 的产物可达（本地文件在位）——隔离不可达退化。
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("produced-with-dry-run-B")
    service = make_upgrade_service(tmp_path, queries)

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

    definition = wfchain_definition({"b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent", capability="cap_b")
    definition = dataclasses.replace(definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
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
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=v1.definition_hash()
    )
    queries.update_job_status(job_id, "completed")
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = make_upgrade_service(tmp_path, queries)

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


def dataclasses_replace_definition(
    definition: WorkflowDefinition, nodes: dict
) -> WorkflowDefinition:
    import dataclasses

    return dataclasses.replace(definition, nodes=nodes)
