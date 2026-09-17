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
    return job


def test_inherit_upgrade_keeps_unchanged_nodes_completed(tmp_path: Path) -> None:
    queries, workspace, revisions, original, service = _inherit_setup(tmp_path)
    # 新 revision 只改 b 的 capability：期望 a 继承，b/c 重跑。
    current = revisions.publish_workspace_revision(
        workspace["id"], _inherit_chain_definition(b_cap="cap_b_new")
    )
    job = _inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
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
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")

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
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
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
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
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
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
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
    queries.update_job_node(job["id"], "a", status="completed")

    results = batch_upgrade(service, workspace["id"], [job["id"]], mode="inherit")

    assert results[0]["mode"] == "inherit"
    assert queries.get_job_node(job["id"], "a")["status"] == "completed"
