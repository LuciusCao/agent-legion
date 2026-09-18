"""inherit 升级模式的服务层测试（issue #645）。

从 ``test_job_workflow_upgrade.py`` 按主题拆出（文件预算）：继承集规划
（变更子图 / 产物可达性退化 / 配置演进基准 / 不可达下游闭包）与重置闭包
的产物清理（review P1-1 / P1-2 / P1-3）。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL


def _inherit_chain_definition(b_cap: str = "cap_b") -> WorkflowDefinition:
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


def _inherit_setup(tmp_path: Path):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], _inherit_chain_definition())
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
    )
    return queries, workspace, revisions, original, service


def _inherit_job(queries, workspace, original, node_keys):
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
    # 播种真实 intake 会冻结的 frozen_config_json（A1 修复后：legacy
    # NULL-frozen 作业的旧侧配置基准不可证明，保守退化为全量重跑——
    # 常规继承用例必须带上 intake 冻结值才有可继承的旧侧基准）。
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


def _seed_impl_identity(queries, workspace, job_id: str, node_keys) -> None:
    """给拟继承节点播种可证明的实现身份（codex 四轮 P1-1 后的测试基准）。

    普通继承用例的 completed 节点现在还要求「执行时身份 == 当前
    published 身份」：published node_code + 最新完成请求携带同一
    code_hash。不播种的节点按「实现不可证明」保守重跑（P1-1 语义，
    判别用例见 codex4 姊妹文件）。
    """
    from tests.services.test_job_workflow_upgrade_inherit_codex4 import (
        _publish_node_code,
        _seed_done_execution,
    )

    for node_key in node_keys:
        code_hash = _publish_node_code(
            queries, workspace["id"], node_key, f"def run(ctx):\n    return {{{node_key!r}}}\n"
        )
        queries.update_job_node(job_id, node_key, status="pending")
        _seed_done_execution(
            queries, workspace["id"], job_id, node_key, kind="code", impl_hash=code_hash
        )


def test_inherit_upgrade_keeps_unchanged_nodes_completed(tmp_path: Path) -> None:
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    # 新 revision 只改 b 的 capability：期望 a 继承，b/c 重跑。
    current = revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    queries.update_job_status(job["id"], "completed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    upgraded = queries.get_job(job["id"])
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert result["status"] == "succeeded"
    assert result["mode"] == "inherit"
    assert result["kept_node_count"] == 1
    assert result["rerun_node_count"] == 2
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    assert upgraded["workflow_revision_id"] == current["id"]
    assert upgraded["status"] == "queued"


def test_inherit_upgrade_reuses_unchanged_revision_by_default_clean(
    tmp_path: Path,
) -> None:
    # 不传 mode（默认 clean）：既有全量重跑行为不变。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")

    result = service.upgrade(workspace["id"], job["id"])

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert result["mode"] == "clean"
    assert result["kept_node_count"] == 0
    assert result["rerun_node_count"] == 3
    assert set(statuses.values()) == {"pending"}
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_degrades_when_artifact_unreachable(tmp_path: Path) -> None:
    # 产物不可达（无本地文件、无清单行）→ 拟继承节点退化重跑。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 的产物 outputs 为空（未声明 outputs）→ 无依赖面，仍继承。
    assert result["kept_node_count"] == 1
    assert statuses["a"] == "completed"
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_degrades_when_outputs_missing(tmp_path: Path) -> None:
    # a 声明 outputs 但产物无本地文件也无清单行 → a 退化重跑（宁可多跑）。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    definition = _inherit_chain_definition()
    # 手动改快照给 a 加 outputs：直接发布带 outputs 的新链。
    import dataclasses

    nodes = {
        "a": dataclasses.replace(definition.nodes["a"], outputs=["a_out.json"]),
        "b": definition.nodes["b"],
        "c": definition.nodes["c"],
    }
    definition_with_outputs = dataclasses.replace(definition, nodes=nodes)
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition_with_outputs)
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 的 outputs 声明出现在新快照（upgrade 后 job 快照已指向新 revision）；
    # 无本地文件 + 无清单行 → a 不可继承，全链重跑。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_inherit_upgrade_uncompleted_candidates_reset_pending(tmp_path: Path) -> None:
    # 继承候选中未完成的节点没有产物可继承 → 重置 pending（与 clean 一致）。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    queries.update_job_node(job["id"], "a", status="failed")
    queries.update_job_status(job["id"], "failed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_inherit_upgrade_skipped_paths_carry_mode(tmp_path: Path) -> None:
    # skipped/failed 结果同样带 mode/统计字段（前端与测试断言的稳定形状）。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)

    missing = service.upgrade(workspace["id"], "missing-job", mode="inherit")

    assert missing["status"] == "failed"
    assert missing["mode"] == "inherit"
    assert missing["kept_node_count"] == 0
    assert missing["rerun_node_count"] == 0


def test_inherit_upgrade_keeps_manifest_rows_of_inherited_nodes(
    tmp_path: Path,
) -> None:
    # 继承节点的 job_artifacts 清单行原样保留（零存储改动）。
    from contextlib import closing

    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a', 'a_out.json', %s, 1, 'hash-a')
            """,
            (job["id"], f"jobs/wschain/{job['id']}/a_out.json"),
        )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["kept_node_count"] == 1
    names = queries.job_artifact_manifest_names_for_nodes(job["id"], {"a"})
    assert names == {("a", "a_out.json")}
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_unreachable_upstream_degrades_its_downstream(tmp_path: Path) -> None:
    # review P1-1：上游产物不可达时，其新图下游闭包一并移出继承集——
    # 上游按新 revision 重跑后，下游继承旧输出会让最终产物基于已被
    # 丢弃的上游结果。
    import dataclasses

    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    definition = _inherit_chain_definition()
    # a 声明 outputs：无本地文件 + 无清单行 → a 不可达；b（下游）自身
    # 产物可达也不许继承。
    nodes = {
        "a": dataclasses.replace(definition.nodes["a"], outputs=["a_out.json"]),
        "b": dataclasses.replace(definition.nodes["b"], outputs=["b_out.json"]),
        "c": definition.nodes["c"],
    }
    original_with_outputs = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=nodes)
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=nodes)
    )
    job = _inherit_job(queries, workspace, original_with_outputs, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    # b 的产物本地存在（b 自身可达），a 的产物不存在。
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b_out.json").write_text("{}")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 不可达 → a 与其下游 b/c 都重跑（c 在新图中也是 a 的下游）。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def _config_schema_chain_definition() -> WorkflowDefinition:
    """a 带 config_schema 的三级链（P1-2 用例的公共构造）。"""
    import dataclasses

    base = _inherit_chain_definition()
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
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
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
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(
        workspace["id"], _config_schema_chain_definition()
    )
    revisions.publish_workspace_revision(workspace["id"], _config_schema_chain_definition())
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
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


def test_inherit_upgrade_stages_and_removes_reset_local_outputs(tmp_path: Path) -> None:
    # review P1-3：服务全链路——重置闭包的本地输出文件暂存并在提交后
    # 删除（旧文件不会被 executor 的输出检查当作本次有效输出）。
    import dataclasses

    from server.app.services.job_artifact_mutation import JobArtifactMutationService

    queries, workspace, revisions, original, _ = _inherit_setup(tmp_path)
    definition = _inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(definition.nodes["a"], outputs=["a_out.json"]),
        "b": dataclasses.replace(definition.nodes["b"], outputs=["b_out.json"]),
        "c": definition.nodes["c"],
    }
    original = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=nodes)
    )
    # b 的 capability 变化 → b/c 重置重跑；a 继承（文件与清单行原样）。
    changed_nodes = {
        **nodes,
        "b": dataclasses.replace(
            definition.nodes["b"], outputs=["b_out.json"], capability="cap_b_new"
        ),
    }
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=changed_nodes)
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    from server.app.storage_paths import resolve_job_dir

    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (("a", "a_out.json"), ("b", "b_out.json")):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job["id"], node_key, name, f"jobs/wschain/{job['id']}/{name}"),
            )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    # 全链未变 → a 继承（文件与清单行原样）；b/c 重置（文件删除、行删除）。
    assert result["kept_node_count"] == 1
    assert (job_dir / "a_out.json").read_text() == "old-a"
    assert not (job_dir / "b_out.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job["id"], {"a", "b"})
    assert names == {("a", "a_out.json")}
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]
    # 暂存目录不残留。
    assert not (job_dir / ".staged").exists()


def test_batch_upgrade_inherit_mode_passes_through(tmp_path: Path) -> None:
    from server.app.services.job_workflow_upgrade_batch import batch_upgrade

    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a"])

    results = batch_upgrade(service, workspace["id"], [job["id"]], mode="inherit")

    assert results[0]["mode"] == "inherit"
    assert queries.get_job_node(job["id"], "a")["status"] == "completed"


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
    base = _inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(base.nodes["a"], config_schema=schema, outputs=["a_out.json"]),
        "b": dataclasses.replace(base.nodes["b"], inputs=["a_out.json"], outputs=["b_out.json"]),
        "c": base.nodes["c"],
    }
    definition = dataclasses.replace(base, nodes=nodes)
    queries, workspace, revisions, _, service = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    current = revisions.publish_workspace_revision(workspace["id"], definition)
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
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


def test_inherit_upgrade_shared_output_name_reruns_both_producers(tmp_path: Path) -> None:
    """A3 + codex P1-3：继承节点与重置节点声明同名纯输出 → 一起重跑。

    A3（对抗审查）发现文件系统暂存面会被同名击穿：stage_outputs 按名字
    收集会把继承节点的产物连带暂存删除。A3 的修法（共享名不暂存、留给
    重跑原地覆盖）只保住了本地文件，却挡不住对象存储串数据：权威对象键
    ``jobs/<ws>/<job>/<name>`` 不含 node 身份，b（重置）重跑上传即按名字
    覆盖共享对象，(a, shared.json) 清单行从此指向 b 的内容；且 b 本次没
    真正写该文件时 ``_check_outputs`` 只查文件存在，会把 a 的旧字节当 b
    本次输出重新上传。codex P1-3 修法：跨闭包同名生产者不能拆开——同名
    output 的闭包外节点并入重跑闭包（保守方向：多跑不串数据）；文件系统
    侧的同名排除（staging_output_names）降级为 rerun/run-to 闭包的既有
    语义 + upgrade 路径的兜底。
    """
    import dataclasses

    from server.app.services.job_artifact_mutation import JobArtifactMutationService
    from server.app.storage_paths import resolve_job_dir
    from server.app.workflows.schema import WorkflowNode

    # b 与 a 都声明 shared.json（b 不声明为输入，保持纯输出形态——
    # RMW 排除（outputs - inputs）会把同名从暂存面拿掉，绕开判定点）；
    # c 独立（无 outputs、不在 a/b 的下游链上），不受波及。
    nodes = {
        "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["shared.json"]),
        "b": WorkflowNode(
            key="b",
            label="B",
            capability="cap_b",
            after=["a"],
            config_schema={},
            outputs=["shared.json"],
        ),
        "c": WorkflowNode(key="c", label="C", capability="cap_c"),
    }
    definition = dataclasses.replace(_inherit_chain_definition(), nodes=nodes, edges=[])
    queries, workspace, revisions, _, _ = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    # b 的 capability 变化 → diff 重置面只有 b；a 与 b 共享 shared.json
    # → a 一并移出继承集；c 独立继承。
    changed = dict(nodes)
    changed["b"] = dataclasses.replace(nodes["b"], capability="cap_b_new")
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=changed)
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    _seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "shared.json").write_text("old-shared")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key in ("a", "b"):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, 'shared.json', %s, 1, 'hash')
                """,
                (job["id"], node_key, f"jobs/wschain/{job['id']}/shared.json"),
            )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # 同名生产者一起重跑：a/b 均 pending；c 独立继承。
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "pending", "b": "pending", "c": "completed"}
    # 两个生产者都在重置面：shared.json 本地暂存删除、清单行同事务清理，
    # 重跑后按新 revision 语义重新产出与上传。
    assert not (job_dir / "shared.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job["id"], {"a", "b"})
    assert names == set()
    assert not (job_dir / ".staged").exists()
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_rename_cleans_orphan_artifact_rows(tmp_path: Path) -> None:
    """A4（对抗审查）：rename a→a2 后旧 key 的 job_artifacts 行不再残留。

    旧缺陷：reset 集按新 key 构建，(a, a_out.json) 行匹配不到重置节点，
    成为永久孤儿（节点 a 在新图中已不存在，清单却继续展示其旧 revision
    产物）。修复：mutation 用事务内既有 job_nodes 行集补出「新 revision
    已消失的旧 key」，其同名暂存行一并删除。
    """
    import dataclasses

    from server.app.services.job_artifact_mutation import JobArtifactMutationService
    from server.app.storage_paths import resolve_job_dir

    base = _inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(base.nodes["a"], outputs=["a_out.json"]),
        "b": dataclasses.replace(base.nodes["b"], inputs=["a_out.json"]),
        "c": base.nodes["c"],
    }
    definition = dataclasses.replace(base, nodes=nodes)
    queries, workspace, revisions, _, _ = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    # rename a→a2（内容等价）：上游集哈希含 key，下游 b/c 必然重跑。
    renamed = dict(nodes)
    renamed["a2"] = dataclasses.replace(nodes["a"], key="a2", label="A2")
    del renamed["a"]
    renamed["b"] = dataclasses.replace(nodes["b"], after=["a2"])
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=renamed, edges=[])
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'a', 'a_out.json', %s, 1, 'hash')
            """,
            (job["id"], f"jobs/wschain/{job['id']}/a_out.json"),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # rename 后全链重跑（命名即身份）；旧 key 的孤儿行被清掉。
    assert result["kept_node_count"] == 0
    assert set(statuses) == {"a2", "b", "c"}
    assert queries.job_artifact_manifest_names_for_nodes(job["id"], {"a"}) == set()
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def _queued_request(conn, workspace_id: str, job_id: str, node_key: str) -> str:
    """插入一条携带旧 revision manifest 的 queued agent 请求（A2 场景）。"""
    import uuid

    execution_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into agent_execution_requests(
          execution_id, workspace_id, job_id, node_key, kind, agent_id,
          agent_definition_hash, node_concurrency_limit, state, queued_at, manifest_json)
        values (%s, %s, %s, %s, 'code', 'cap_b_old', %s, 1, 'queued',
                current_timestamp, %s)
        """,
        (
            execution_id,
            workspace_id,
            job_id,
            node_key,
            "o" * 64,
            json.dumps({"kind": "code", "node_key": node_key, "capability": "cap_b_old"}),
        ),
    )
    return execution_id


def test_inherit_upgrade_cancels_queued_agent_requests(tmp_path: Path) -> None:
    """A2（对抗审查）：升级必须取消重置节点的 queued agent 请求。

    旧 revision 下入队的请求 manifest 携带旧语义（capability/config/
    skill pin），claim 侧复查链（job queued、无 active lease、节点
    pending/ready/stale）在升级后的新 pending 行上全部放行——不取消就
    会以旧 revision 语义执行并把产物挂到新 revision 作业（与 rerun 路径
    mark_nodes_for_rerun 的取消同款，clean 模式自 base 起同样缺失）。
    """
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    queries.update_job_node(job["id"], "a", status="completed")
    queries.update_job_status(job["id"], "queued")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        execution_id = _queued_request(conn, workspace["id"], job["id"], "b")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    with closing(connect_database(queries.dsn_identity)) as conn:
        state = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()["state"]
    assert state == "cancelled"


def test_clean_upgrade_cancels_queued_agent_requests(tmp_path: Path) -> None:
    # A2 的 clean 模式配对：自 base 起同样缺失，本次一并补上。
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    queries.update_job_status(job["id"], "queued")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        execution_id = _queued_request(conn, workspace["id"], job["id"], "b")

    result = service.upgrade(workspace["id"], job["id"], mode="clean")

    assert result["status"] == "succeeded"
    with closing(connect_database(queries.dsn_identity)) as conn:
        state = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()["state"]
    assert state == "cancelled"


def test_inherit_upgrade_code_republish_with_inserted_node_reruns_all(tmp_path: Path) -> None:
    """702 用户反例端到端：实现身份种子必须经闭包传播到全部下游。

    反例图：A(code)→B(agent)→C、B→D。A 的 node_code 重发布 V1→V2
    （**工作流定义不变**）+ 新 revision 在 B/C 间插入 E。旧行为的架构
    缺陷：B 的上游一致性由定义侧基准间接证明——A 的身份排除若只做
    候选过滤而不并入种子，B/C/D 会被错误继承，B 的 input（A 的新产物）
    与 B 的产物不一致。重构后：A 进 S4 种子 → 闭包把 B/C/D 全部带进
    重置面；E 是新增节点（S1）同样重跑——kept == 0。
    """
    from server.app.agent_catalog import AgentDefinition
    from server.app.services.job_artifact_mutation import JobArtifactMutationService
    from server.app.storage_paths import resolve_job_dir
    from tests.helpers import replace_agent_catalog
    from tests.services.test_job_workflow_upgrade_inherit_codex4 import (
        _publish_node_code,
        _seed_local_pool_execution,
    )

    def _graph(with_e: bool) -> WorkflowDefinition:
        nodes = {
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b",
                label="B",
                node_type="agent",
                capability="cap_b",
                after=["a"],
                outputs=["b_out.json"],
            ),
            "c": WorkflowNode(
                key="c", label="C", capability="cap_c", after=["b"], outputs=["c_out.json"]
            ),
            "d": WorkflowNode(key="d", label="D", capability="cap_d", after=["b"]),
        }
        if with_e:
            nodes["e"] = WorkflowNode(
                key="e", label="E", capability="cap_e", after=["b"], outputs=["e_out.json"]
            )
            nodes["c"] = WorkflowNode(
                key="c", label="C", capability="cap_c", after=["e"], outputs=["c_out.json"]
            )
        return WorkflowDefinition(
            key="wfchain",
            label="Wf Chain",
            intake=WorkflowIntake(),
            nodes=nodes,
        )

    queries, workspace, revisions, original, _ = _inherit_setup(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], _graph(with_e=False))
    # A 的 code V1 发布 + B 的 published Agent（当前身份）。
    a_v1 = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {'v': 1}\n")
    agent_v1 = AgentDefinition(capability="cap_b", runtime="pi", skill="g/n")
    replace_agent_catalog(workspace["id"], {"agent-b": agent_v1})
    job = _inherit_job(queries, workspace, original, ["a", "b", "c", "d"])
    # 播种执行记录：A 记 V1 hash（本地池形态，node_runs 直查命中）；
    # B/C/D 记各自当前身份（done 请求行——Worker/Agent 形态）。
    from tests.services.test_job_workflow_upgrade_inherit_codex4 import _seed_done_execution

    queries.update_job_node(job["id"], "a", status="pending")
    _seed_local_pool_execution(queries, job["id"], "a", a_v1)
    for node_key in ("b", "c", "d"):
        queries.update_job_node(job["id"], node_key, status="pending")
    _seed_done_execution(
        queries,
        workspace["id"],
        job["id"],
        "b",
        kind="agent",
        impl_hash=agent_v1.definition_hash(),
    )
    c_hash = _publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {'c'}\n")
    _seed_done_execution(queries, workspace["id"], job["id"], "c", kind="code", impl_hash=c_hash)
    d_hash = _publish_node_code(queries, workspace["id"], "d", "def run(ctx):\n    return {'d'}\n")
    _seed_done_execution(queries, workspace["id"], job["id"], "d", kind="code", impl_hash=d_hash)
    queries.update_job_status(job["id"], "completed")
    # 产物可达（隔离不可达退化维度）。
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in ("a_out.json", "b_out.json", "c_out.json"):
        (job_dir / name).write_text(f"old-{name}")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (
            ("a", "a_out.json"),
            ("b", "b_out.json"),
            ("c", "c_out.json"),
        ):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key,
                                          size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job["id"], node_key, name, f"jobs/wschain/{job['id']}/{name}"),
            )
    # 变更：A 的 node_code 重发布 V1→V2（工作流定义不动）+ 插入 E。
    _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {'v': 2}\n")
    current = revisions.publish_workspace_revision(workspace["id"], _graph(with_e=True))
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # 反例闭合：A 身份漂移（S4 种子）→ B/C/D（全部下游）+ E（新增 S1）
    # 一起重跑，无任何节点被错误继承。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    assert statuses == {
        "a": "pending",
        "b": "pending",
        "c": "pending",
        "d": "pending",
        "e": "pending",
    }
    # 重置节点的本地产物进暂存删除（不会以旧实现字节冒充新产物）。
    assert not (job_dir / "a_out.json").exists()
    assert not (job_dir / "b_out.json").exists()
    assert not (job_dir / "c_out.json").exists()
    assert not (job_dir / ".staged").exists()
    # job_artifacts 清单行同事务清理。
    names = queries.job_artifact_manifest_names_for_nodes(job["id"], {"a", "b", "c", "d", "e"})
    assert names == set()
    # job 重 pin 新 revision + frozen 更新。
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_revision_id"] == current["id"]
    assert upgraded["status"] == "queued"
