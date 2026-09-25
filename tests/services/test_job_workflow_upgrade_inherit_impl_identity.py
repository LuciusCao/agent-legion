"""inherit 升级的实现身份判定测试（issue #645 codex 四轮 P1-1，#645 v85）。

执行时身份（node_runs.agent_definition_hash 优先、请求行 fallback、本地池
记录）vs 当前 published 身份：相等才继承，漂移/不可证明即重跑并传播下游。
自 ``test_job_workflow_upgrade_inherit_codex4.py`` 按主题拆出（零改动迁移）。
"""

from pathlib import Path

from server.app.agent_catalog import AgentDefinition
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    publish_node_code,
    seed_done_execution,
    seed_local_pool_execution,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_definition,
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
    definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    queries.update_job_status(job_id, "completed")
    # 产物可达性：a/b 的本地输出文件在位（否则不可达退化会重跑）。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = make_upgrade_service(tmp_path, queries)

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

    definition = wfchain_definition({"b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    old_hash = publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=old_hash)
    queries.update_job_status(job_id, "completed")
    # b 的产物可达（本地文件在位）——隔离不可达退化，判别点收敛在
    # 实现漂移上：去掉 P1-1 时 b 本应被继承。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-by-v1")
    # b 的实现重发布（code 文本变化、workflow 定义未动）。
    publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n")
    service = make_upgrade_service(tmp_path, queries)

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
    definition = wfchain_definition()
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    service = make_upgrade_service(tmp_path, queries)

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

    definition = wfchain_definition()
    agent_nodes = dict(definition.nodes)
    agent_nodes["b"] = dataclasses.replace(
        definition.nodes["b"], node_type="agent", capability="cap_b"
    )
    definition = dataclasses.replace(definition, nodes=agent_nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    v1 = AgentDefinition(capability="cap_b", runtime="pi")
    replace_agent_catalog(workspace["id"], {"agent-b": v1})
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(
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
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


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
    definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_local_pool_execution(queries, job_id, "a", a_hash)
    seed_local_pool_execution(queries, job_id, "b", b_hash)
    queries.update_job_status(job_id, "completed")
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    service = make_upgrade_service(tmp_path, queries)

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
    definition = wfchain_definition({"b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    old_hash = publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_local_pool_execution(queries, job_id, "b", old_hash)
    queries.update_job_status(job_id, "completed")
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("produced-by-v1")
    # b 的实现重发布（workflow 定义未动）→ node_runs 记录的 V1 hash 漂移。
    publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n")
    service = make_upgrade_service(tmp_path, queries)

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
    definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    v1_hash = publish_node_code(
        queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 1}\n"
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # a：历史形态（只有请求行）；b：请求行记录 V1 + node_runs 记录 V1。
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=v1_hash)
    queries.update_job_status(job_id, "completed")
    # b 重发布 V2：此时补一条带 V2 身份的 node_runs completed 行（模拟
    # v85 后的又一次本地池执行）——node_runs 段最新 completed 记录 V2。
    # （先把 b 拉回 pending：done 请求播种只置 run 行 completed，这里
    # 复位 job_node 以便 start_node_run 的 pending→running 守卫放行。）
    queries.update_job_node(job_id, "b", status="pending")
    seed_local_pool_execution(
        queries,
        job_id,
        "b",
        publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {'v': 2}\n"),
    )
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    # b 的产物在位（本地池执行播种不写文件，这里补上——隔离不可达退化，
    # 判别点收敛在读取端的段合并优先级上）。
    (job_dir / "b_out.json").write_text("old-b")
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a 走请求行 fallback、身份匹配 → 继承；b 的 node_runs 段最新记录 V2
    # 与当前 published 相等 → 继承（若误用请求行 V1 会误判漂移重跑）。
    assert result["kept_node_count"] == 2
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    assert statuses["c"] == "pending"
    assert (job_dir / "b_out.json").read_text() == "old-b"
