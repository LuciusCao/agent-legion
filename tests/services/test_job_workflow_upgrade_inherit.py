"""inherit 升级模式的核心语义与可达性退化测试（issue #645）。

继承集规划（变更子图 / 产物可达性退化 / 不可达下游闭包）、clean 默认
行为、批量模式透传与 skipped 结果形状。frozen 配置基准见
``test_job_workflow_upgrade_inherit_config.py``；重置面清理（暂存/同名/
rename/queued 取消/实现重发布）见 ``test_job_workflow_upgrade_inherit_cleanup.py``。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers.job_workflow_upgrade import (
    inherit_chain_definition,
    seed_impl_identity,
    seed_inherit_job,
    setup_inherit_env,
)
from tests.postgres_support import TEST_DATABASE_URL


def test_inherit_upgrade_keeps_unchanged_nodes_completed(tmp_path: Path) -> None:
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    # 新 revision 只改 b 的 capability：期望 a 继承，b/c 重跑。
    current = revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
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
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
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
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 的产物 outputs 为空（未声明 outputs）→ 无依赖面，仍继承。
    assert result["kept_node_count"] == 1
    assert statuses["a"] == "completed"
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_degrades_when_outputs_missing(tmp_path: Path) -> None:
    # a 声明 outputs 但产物无本地文件也无清单行 → a 退化重跑（宁可多跑）。
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    definition = inherit_chain_definition()
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
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 的 outputs 声明出现在新快照（upgrade 后 job 快照已指向新 revision）；
    # 无本地文件 + 无清单行 → a 不可继承，全链重跑。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_inherit_upgrade_requires_manifest_row_when_authority_enabled(tmp_path: Path) -> None:
    # codex 复审 P2（#776）：对象存储权威层启用时，仅本地缓存文件不足证可达。
    # completed 节点只有本地文件、无 job_artifacts 清单行（上传失败/补传未完
    # 成）时，本地副本是可淘汰缓存、hydration 只能按清单行恢复——判为可继承
    # 会在淘汰后让下游永久等输入。修复：权威层启用时 keep 要求清单行存在，
    # 否则退化重跑（宁可多跑）。
    import dataclasses

    from server.app.services.job_artifact_objects import JobArtifactObjectStore
    from server.app.storage_paths import resolve_job_dir
    from tests.fakes.storage import FakeObjectStorage

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    definition = inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(definition.nodes["a"], outputs=["a_out.json"]),
        "b": definition.nodes["b"],
        "c": definition.nodes["c"],
    }
    original = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=nodes)
    )
    revisions.publish_workspace_revision(
        workspace["id"],
        dataclasses.replace(
            definition,
            nodes={**nodes, "b": dataclasses.replace(nodes["b"], capability="cap_b_new")},
        ),
    )
    # 权威层启用（enabled store），但不 seed 任何清单行——a 只有本地文件。
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        object_store=JobArtifactObjectStore(queries, FakeObjectStorage()),
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("{}")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # a 有本地文件但无清单行 → 权威层启用时不可证可达 → a 退化，全链重跑。
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_inherit_upgrade_uncompleted_candidates_reset_pending(tmp_path: Path) -> None:
    # 继承候选中未完成的节点没有产物可继承 → 重置 pending（与 clean 一致）。
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    queries.update_job_node(job["id"], "a", status="failed")
    queries.update_job_status(job["id"], "failed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert result["kept_node_count"] == 0
    assert set(statuses.values()) == {"pending"}


def test_inherit_upgrade_skipped_paths_carry_mode(tmp_path: Path) -> None:
    # skipped/failed 结果同样带 mode/统计字段（前端与测试断言的稳定形状）。
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)

    missing = service.upgrade(workspace["id"], "missing-job", mode="inherit")

    assert missing["status"] == "failed"
    assert missing["mode"] == "inherit"
    assert missing["kept_node_count"] == 0
    assert missing["rerun_node_count"] == 0


def test_inherit_upgrade_keeps_manifest_rows_of_inherited_nodes(
    tmp_path: Path,
) -> None:
    # 继承节点的 job_artifacts 清单行原样保留（零存储改动）。

    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    current = revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
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

    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    definition = inherit_chain_definition()
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
    job = seed_inherit_job(queries, workspace, original_with_outputs, ["a", "b", "c"])
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


def test_batch_upgrade_inherit_mode_passes_through(tmp_path: Path) -> None:
    from server.app.services.job_workflow_upgrade_batch import batch_upgrade

    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a"])

    results = batch_upgrade(service, workspace["id"], [job["id"]], mode="inherit")

    assert results[0]["mode"] == "inherit"
    assert queries.get_job_node(job["id"], "a")["status"] == "completed"


def test_inherit_upgrade_label_only_change_keeps_job_completed(tmp_path: Path) -> None:
    """codex #776 复审 P1：全部节点可继承（零重跑）时不得把作业改 queued。

    仅改展示字段（label）的升级：定义哈希两侧相等（label 排除在节点哈希
    外）→ 无种子 → 全部继承、reset_nodes 为空。零重跑不会再产生任何
    lease 完成事件来 sync_job_status，workflow worker 的就绪评估也不聚合
    作业状态——无条件 queued 会让已完成作业永久显示排队中。修复：零重
    跑时按保留节点终态（全 completed）推导，作业保持 completed。
    """
    import dataclasses

    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    definition = inherit_chain_definition()
    # 只改 label（展示字段，不进节点定义哈希）。
    nodes = {
        key: dataclasses.replace(node, label=f"{node.label} v2")
        for key, node in definition.nodes.items()
    }
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=nodes)
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 3
    assert result["rerun_node_count"] == 0
    upgraded = queries.get_job(job["id"])
    assert upgraded["workflow_revision_id"] == current["id"]
    # 零重跑：作业状态保持 completed（而非永久 queued）。
    assert upgraded["status"] == "completed"
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    assert set(statuses.values()) == {"completed"}
