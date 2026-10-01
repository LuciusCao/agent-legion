"""inherit 升级的 RMW 产物保留与同名闭包测试（codex 五轮 P1-B / 六轮 P1）。

升级后变成 RMW 输入的旧产物保留为启动输入（#114 语义对齐）；RMW 输出纳入
同名生产者闭包——保留侧与重置侧同名（含 RMW）一起重跑。自
``test_job_workflow_upgrade_inherit_codex5.py`` 按主题拆出（零改动迁移）。
"""

import dataclasses
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    publish_node_code,
    seed_local_pool_execution,
    seed_reachable_outputs,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_definition,
)

# ---------------------------------------------------------------------------
# P1-B：升级后变成 RMW 输入的旧产物保留
# ---------------------------------------------------------------------------


def test_upgrade_to_rmw_input_keeps_old_artifact_as_startup_input(tmp_path: Path) -> None:
    """codex 五轮 P1-B：旧 outputs=["x"] → 新 inputs=["x"], outputs=["x"]。

    旧缺陷：``removed_artifact_face`` 的重置节点分支用 ``_pure_outputs(
    new_node)``（outputs − inputs）做差——RMW 名 x 被排除出「新纯输出」
    → 判为已移除产物 → 本地文件进暂存 + 清单行删除 + 对象清理，重置后
    的 RMW 节点缺启动输入（x 没有别的生产者，restore 也无清单可回）。
    修复：清理面排除新节点声明的 RMW 名（新 inputs ∪ outputs 口径），
    与 rerun 保留 RMW 输入的 #114 语义一致。
    """
    old_definition = wfchain_definition({"b": ["x.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：b 变更（capability 变化进重置面）且 x 变 RMW。
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"],
        capability="cap_b_new",
        inputs=["x.json"],
        outputs=["x.json"],
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = seed_reachable_outputs(queries, job_id, ["x.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # b/c 重跑（b 变更）；x.json 是重置后 RMW 节点的启动输入：本地文件
    # 与清单行都保留（重跑后节点原地重写，#114 语义）。
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    assert (job_dir / "x.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_upgrade_to_rmw_input_still_cleans_other_removed_outputs(tmp_path: Path) -> None:
    """P1-B 精度对照：同节点同时有 RMW 化与真正移除的 output 名。

    旧 outputs=[x, y]、新 inputs=[x], outputs=[x]：x 进 RMW 保留（启动
    输入），y 不再被声明 → y 走正常清理面（文件 + 清单行）。证明排除
    面是「新 RMW 名」本身，不是「重置节点带 RMW 即整体跳过」的粗面。
    """
    old_definition = wfchain_definition({"b": ["x.json", "y.json"]})
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    new_nodes = dict(old_definition.nodes)
    new_nodes["b"] = dataclasses.replace(
        old_definition.nodes["b"],
        capability="cap_b_new",
        inputs=["x.json"],
        outputs=["x.json"],
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = seed_reachable_outputs(queries, job_id, ["x.json", "y.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for name in ("x.json", "y.json"):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, 'b', %s, %s, 1, 'hash')
                """,
                (job_id, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = make_upgrade_service(tmp_path, queries)

    service.upgrade(workspace["id"], job_id, mode="inherit")

    # x（RMW 输入）保留；y（不再声明）清理——文件与清单行都消失。
    assert (job_dir / "x.json").exists()
    assert not (job_dir / "y.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names
    assert ("b", "y.json") not in names


# ---------------------------------------------------------------------------
# codex 六轮 P1：RMW 输出纳入同名生产者闭包
# ---------------------------------------------------------------------------


def test_inherit_rmw_output_same_name_as_reset_pure_output_reruns_together(
    tmp_path: Path,
) -> None:
    """codex 六轮 P1：被继承节点声明 RMW（同名为 input+output），重置节点
    把该名声明为普通 output。

    旧缺陷：``shared_name_rerun_closure`` 的名字面用 ``outputs - inputs``
    （纯输出）——RMW 名被完全忽略，RMW 候选不会被拉进重跑面。随后暂存
    移走共享名的本地文件、mutation 只删重置节点清单行、提交后按共享对象
    键删除——被保留的 completed RMW 节点有清单行却无可达产物（本地文件
    在 .staged、权威对象已被 best-effort 清理）。修复：跨继承/重置边界
    的冲突检测覆盖 RMW output（生产者面 = 全部 outputs），RMW 节点与其
    下游一起重跑。

    图：a → rmw(b)，b 声明 inputs=[x.json], outputs=[x.json]；c 重置后
    与 b 共享 x.json（纯输出）。b 是唯一可继承候选（新旧定义与实现身份
    全部可证明）；c 变更进重置面。

    与 P1-B（本文件 ``test_upgrade_to_rmw_input_keeps_old_artifact_as_
    startup_input``）的边界：那是「清理面不删 RMW 节点自己的启动输入」
    （removed_artifact_face 排除 _rmw_names），这是「名字闭包把冲突对方
    拉进重跑」——两个方向互补，不得互相打架。
    """
    old_definition = WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                inputs=["x.json"],
                outputs=["x.json"],
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：c 变更（capability 变化进重置面）并把 x.json 声明为
    # 普通 output——与被继承的 RMW 节点 b 共享对象键。
    new_nodes = dict(old_definition.nodes)
    new_nodes["c"] = dataclasses.replace(
        old_definition.nodes["c"], capability="cap_c_new", outputs=["x.json"]
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    # a/b 的实现身份可证明（publish + 记录一致）→ a/b 都是继承候选；
    # c 变更（S1 种子）进重置面并声明 x.json 为普通 output。

    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_local_pool_execution(queries, job_id, "a", a_hash)
    seed_local_pool_execution(queries, job_id, "b", b_hash)
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = seed_reachable_outputs(queries, job_id, ["x.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 端到端断言：RMW 节点 b 必须被拉进重跑（x.json 与重置节点 c 冲突），
    # 其下游语义随之作废——b/c pending，只有无冲突的 a 保留。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert statuses == {"a": "completed", "b": "pending", "c": "pending"}
    # b 虽进入重跑面，x.json 仍是它的 RMW 启动输入，必须保留文件与清单
    # 行；否则节点会永久等待一个没有上游重新生产的输入（#114/P1-B）。
    assert (job_dir / "x.json").read_text() == "old-x.json"
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b", "c"})
    assert names == {("b", "x.json")}
    assert queries.get_job(job_id)["workflow_revision_id"] == current["id"]


def test_inherit_rmw_node_without_name_conflict_stays_inherited(tmp_path: Path) -> None:
    """codex 六轮 P1 对照组：RMW 节点的名字无跨边界冲突 → 照常继承。

    证明判别点是「冲突检测覆盖 RMW output」，而非「RMW 节点一律排除」
    的粗面——重置节点不声明 x.json 时，b 的 RMW 语义（启动输入保留，
    #114/P1-B）原样成立：本地文件与清单行保留、节点 completed。
    """
    old_definition = WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a"),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                inputs=["x.json"],
                outputs=["x.json"],
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, old_definition)
    # 新 revision：c 变更但输出名与 b 的 RMW 名不冲突。
    new_nodes = dict(old_definition.nodes)
    new_nodes["c"] = dataclasses.replace(
        old_definition.nodes["c"], capability="cap_c_new", outputs=["c_out.json"]
    )
    revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(old_definition, nodes=new_nodes)
    )
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])

    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    b_hash = publish_node_code(queries, workspace["id"], "b", "def run(ctx):\n    return {}\n")
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_local_pool_execution(queries, job_id, "a", a_hash)
    seed_local_pool_execution(queries, job_id, "b", b_hash)
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    queries.update_job_status(job_id, "completed")
    job_dir = seed_reachable_outputs(queries, job_id, ["x.json", "c_out.json"])
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'b', 'x.json', %s, 1, 'hash')
            """,
            (job_id, f"jobs/wschain/{job_id}/x.json"),
        )
    service = make_upgrade_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a/b 身份均可证明且 b 的 RMW 名无冲突 → 两者继承；c 变更重跑。
    assert result["kept_node_count"] == 2
    assert statuses == {"a": "completed", "b": "completed", "c": "pending"}
    assert (job_dir / "x.json").read_text() == "old-x.json"
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"b"})
    assert ("b", "x.json") in names
