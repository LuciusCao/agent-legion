"""inherit 升级的旧 output 名与被删节点清理测试（issue #645 codex 四轮 P1-2）。

旧快照被移除 output 名与被删节点的本地产物/清单行/runs 目录清理面，
含「其余全继承时被删节点面不遗留」的 CRITICAL-1 回归。自
``test_job_workflow_upgrade_inherit_codex4.py`` 按主题拆出（零改动迁移）。
"""

import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.workflows.schema import WorkflowNode
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    publish_node_code,
    seed_done_execution,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_definition,
)

# ---------------------------------------------------------------------------
# P1-2 旧快照被移除 output 名 / 被删节点的清理
# ---------------------------------------------------------------------------


def test_removed_output_name_cleaned_local_and_manifest(tmp_path: Path) -> None:
    """codex 四轮 P1-2：重置节点 output 从 old.json 改成 new.json → old.json 清理。

    旧缺陷：stage_outputs 只按新 definition 得暂存名，old.json 不在
    staged_artifact_names——清单行与本地文件保留，API 继续展示旧产物，
    新图同名外部输入还会消费旧字节。修复：从旧快照补出被移除 output 名。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = wfchain_definition({"b": ["old.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：b 的 capability 变（进重置面）且 output 改名。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"], capability="cap_b_new", outputs=["new.json"]
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old.json").write_text("stale-bytes")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'old.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/old.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # old.json 的本地文件与清单行都被清理；节点 b 重置（capability 变化）。
    assert result["kept_node_count"] == 0
    assert not (job_dir / "old.json").exists()
    assert not (job_dir / ".staged").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert names == set()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_removed_output_name_of_inherited_sibling_not_cleaned(tmp_path: Path) -> None:
    """codex 四轮 P1-2 配对（A3 口径）：保留节点声明的名字不进清理面。

    a（继承候选）声明 old.json 为输出；b（重置）的旧快照也产出过
    old.json 但新图删掉了它——该名字是 a 的产物/声明面，清掉会把
    completed 节点的清单行指向空文件。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = wfchain_definition({"a": ["old.json"], "b": ["old.json", "b_out.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：只有 b 变（capability），a 定义未变 → a 是继承候选。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"], capability="cap_b_new", outputs=["b_out.json"]
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    # a 的实现身份可证明（P1-1）：published node_code + 匹配的完成记录。
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old.json").write_text("a-artifact")
    (job_dir / "b_out.json").write_text("old-b")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (("a", "old.json"), ("b", "old.json"), ("b", "b_out.json")):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job_id, node_key, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # a 继承（old.json 是它的产物——本地文件与清单行原样）；b 重置：
    # b_out.json 走既有暂存路径清理，(b, old.json) 清单行保留（名字属于 a）。
    assert result["kept_node_count"] == 1
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert statuses["a"] == "completed"
    assert (job_dir / "old.json").read_text() == "a-artifact"
    assert not (job_dir / "b_out.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b"})
    assert ("a", "old.json") in names
    assert ("b", "old.json") in names  # 名字被保留节点声明 → 不清理（A3 口径）


def test_deleted_node_outputs_and_runs_cleaned(tmp_path: Path) -> None:
    """codex 四轮 P1-2：生产节点被删 → 其全部纯输出名与 runs 目录清理。

    A4 的 renamed_from_nodes 只按新节点暂存名匹配清单行；节点删除后其
    自身声明名的本地文件与 runs/<key> 历史目录此前全部遗留。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = wfchain_definition({"x": ["x_out.json"], "a": [], "b": [], "c": []})
    nodes = dict(old_definition.nodes)
    nodes["x"] = WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"])
    old_definition = dataclasses.replace(old_definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：删除 x。
    new_nodes = {k: v for k, v in old_definition.nodes.items() if k != "x"}
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c", "x"])
    for key in ("a", "b", "c", "x"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x_out.json").write_text("stale-x")
    (job_dir / "runs" / "x").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "x" / "log.txt").write_text("history")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'x', 'x_out.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x_out.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    service.upgrade(workspace["id"], job_id, mode="inherit")

    # 被删节点：清单行、本地产物文件与 runs 历史目录全部清理。
    assert queries.job_artifact_manifest_names_for_nodes(job_id, {"x"}) == set()
    assert not (job_dir / "x_out.json").exists()
    assert not (job_dir / "runs" / "x").exists()
    # runs/x 的暂存件已 commit 删除（.staged/runs 子目录壳是 StagedOutputs
    # 既有行为，与产物清理语义无关，不在此断言）。
    assert not (job_dir / ".staged" / "runs" / "x").exists()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_deleted_node_outputs_cleaned_even_when_rest_inherited(tmp_path: Path) -> None:
    """codex 四轮复审 CRITICAL-1：被删节点的清理独立于重置面。

    只删终端生产节点、其余节点定义未变且身份可证明 → reset_keys 为空
    （all-keep 路径）。旧 guard ``not reset_keys`` 在 removed_artifact_face
    之前早退，被删节点的本地文件 / runs 目录 / 清单行全部遗留——新
    revision 没有任何东西会再生产 x_out.json，但 API 继续把它展示为该
    job 的产物。修法：removed 面非空时即使 reset_keys 为空也走
    stage_outputs（暂存面只含 extra，安全）。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    nodes = dict(old_definition.nodes)
    # x 放在 a/b/c 之后：job 快照派生定义的节点序与既有用例一致（a/b/c
    # 的继承不受 x 加入节点字典顺序的影响——x 本身不在新定义的可执行集）。
    nodes["x"] = WorkflowNode(key="x", label="X", capability="cap_x", outputs=["x_out.json"])
    old_definition = dataclasses.replace(old_definition, nodes=nodes)
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：只删除终端节点 x，a/b 定义未变。
    new_nodes = {k: v for k, v in old_definition.nodes.items() if k != "x"}
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    # a/b/c 的实现身份均可证明（P1-1）——判别点收敛在 CRITICAL-1 的清理面
    # 上：reset_keys 为空（真正的 all-keep 路径），去掉本修复时被删节点 x
    # 的清理被 all-keep guard 短路（文件 / runs / 清单行全部遗留）。
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    c_hash = publish_node_code(queries, workspace["id"], "c", "def run(ctx):\n    return {}\n")
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c", "x"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    # _seed_done_execution 置 completed；x 直接置 completed（无执行记录，
    # 但 x 已从新定义消失——被删节点的清理不看身份记录，只看新旧定义差）。
    queries.update_job_node(job_id, "x", status="completed")
    queries.update_job_status(job_id, "completed")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(queries, workspace["id"], job_id, "b", kind="code", impl_hash=b_hash)
    seed_done_execution(queries, workspace["id"], job_id, "c", kind="code", impl_hash=c_hash)
    # 播种 intake 冻结（无 x 的段）：x 不在新定义，其冻结段不该让旧侧
    # 基准判为「有 config 面」而整体退化 clean——直接按 a/b/c 冻结播种
    # 与真实 intake 后删节点的演进序列一致（发布新 revision 前的旧 job
    # 不会带未出生节点的冻结段）。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (
                json.dumps(
                    {
                        "a": {"sandbox_network": False, "timeout_seconds": 600},
                        "b": {"sandbox_network": False, "timeout_seconds": 600},
                        "c": {"sandbox_network": False, "timeout_seconds": 600},
                    }
                ),
                job_id,
            ),
        )
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    (job_dir / "x_out.json").write_text("stale-x")
    (job_dir / "runs" / "x").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "x" / "log.txt").write_text("history")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'x', 'x_out.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x_out.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # a/b/c 全部继承（身份可证明、定义未变、产物可达）——reset_keys 为空
    # 的 all-keep 路径。核心断言：被删节点 x 的清理面完整——文件、runs
    # 目录、清单行，不被 all-keep guard 短路。
    assert result["kept_node_count"] == 3
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert statuses == {"a": "completed", "b": "completed", "c": "completed"}
    assert (job_dir / "a_out.json").read_text() == "old-a"
    assert not (job_dir / "x_out.json").exists()
    assert not (job_dir / "runs" / "x").exists()
    assert queries.job_artifact_manifest_names_for_nodes(job_id, {"x"}) == set()
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]
