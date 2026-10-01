"""inherit 升级的重置面清理测试（issue #645 review P1-1/P1-2/P1-3 等）。

重置节点的本地产物暂存与清单行删除、跨闭包同名输出一起重跑（通道 B）、
rename 孤儿行清理（A4）、queued agent 请求了结（A2）、实现重发布 + 新增
节点的组合退化。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers.job_workflow_upgrade import (
    inherit_chain_definition,
    seed_impl_identity,
    seed_inherit_job,
    setup_inherit_env,
)


def test_inherit_upgrade_stages_and_removes_reset_local_outputs(tmp_path: Path) -> None:
    # review P1-3：服务全链路——重置闭包的本地输出文件暂存并在提交后
    # 删除（旧文件不会被 executor 的输出检查当作本次有效输出）。
    import dataclasses

    from server.app.services.job_artifact_mutation import JobArtifactMutationService

    queries, workspace, revisions, original, _ = setup_inherit_env(tmp_path)
    definition = inherit_chain_definition()
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
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
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
    definition = dataclasses.replace(inherit_chain_definition(), nodes=nodes, edges=[])
    queries, workspace, revisions, _, _ = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    # b 的 capability 变化 → diff 重置面只有 b；a 与 b 共享 shared.json
    # → a 一并移出继承集；c 独立继承。
    changed = dict(nodes)
    changed["b"] = dataclasses.replace(nodes["b"], capability="cap_b_new")
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=changed)
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
    seed_impl_identity(queries, workspace, job["id"], ["a", "b", "c"])
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

    base = inherit_chain_definition()
    nodes = {
        "a": dataclasses.replace(base.nodes["a"], outputs=["a_out.json"]),
        "b": dataclasses.replace(base.nodes["b"], inputs=["a_out.json"]),
        "c": base.nodes["c"],
    }
    definition = dataclasses.replace(base, nodes=nodes)
    queries, workspace, revisions, _, _ = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    # rename a→a2（内容等价）：上游集哈希含 key，下游 b/c 必然重跑。
    renamed = dict(nodes)
    renamed["a2"] = dataclasses.replace(nodes["a"], key="a2", label="A2")
    del renamed["a"]
    renamed["b"] = dataclasses.replace(nodes["b"], after=["a2"])
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(definition, nodes=renamed, edges=[])
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
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
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
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
    queries, workspace, revisions, original, service = setup_inherit_env(tmp_path)
    revisions.publish_workspace_revision(
        workspace["id"], inherit_chain_definition(b_cap="cap_b_new")
    )
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c"])
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
    from tests.helpers.job_workflow_upgrade import (
        publish_node_code as _publish_node_code,
    )
    from tests.helpers.job_workflow_upgrade import (
        seed_local_pool_execution as _seed_local_pool_execution,
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

    queries, workspace, revisions, original, _ = setup_inherit_env(tmp_path)
    original = revisions.publish_workspace_revision(workspace["id"], _graph(with_e=False))
    # A 的 code V1 发布 + B 的 published Agent（当前身份）。
    a_v1 = _publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {'v': 1}\n")
    agent_v1 = AgentDefinition(capability="cap_b", runtime="pi", skill="g/n")
    replace_agent_catalog(workspace["id"], {"agent-b": agent_v1})
    job = seed_inherit_job(queries, workspace, original, ["a", "b", "c", "d"])
    # 播种执行记录：A 记 V1 hash（本地池形态，node_runs 直查命中）；
    # B/C/D 记各自当前身份（done 请求行——Worker/Agent 形态）。
    from tests.helpers.job_workflow_upgrade import seed_done_execution as _seed_done_execution

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


def test_inherit_upgrade_node_converted_to_start_is_treated_as_deleted(tmp_path: Path) -> None:
    """codex #776 复审 P2（R5）：executable→start 转换按删除旧执行节点处理。

    旧 revision 的 d 是可执行生产节点（outputs x.json/y.json）；新 revision
    保留同 key 但改为 ``type: start``（入口）——全节点 key 差集识别不到它，
    它又不在新图 executable_nodes/reset 面：旧 outputs 清单行、run 目录与
    queued 请求全部逃逸清理。新图把 x.json 改作外部输入（保护计划保留为
    种子——与「被删生产者的名被消费即保留」同设计），y.json 无任何引用
    必须退役。修复：删除面比较 executable_nodes（转换即删除旧执行节点）。
    """
    from server.app.services.job_artifact_mutation import JobArtifactMutationService
    from server.app.storage_paths import resolve_job_dir
    from server.app.workflows.definition import workflow_definition_from_dict

    queries, workspace, revisions, original, _ = setup_inherit_env(tmp_path)
    old = workflow_definition_from_dict(
        {
            "key": "wfchain",
            "label": "Wf Chain",
            "nodes": {
                "a": {"label": "A", "capability": "cap_a"},
                "d": {
                    "label": "D",
                    "capability": "cap_d",
                    "after": ["a"],
                    "outputs": ["x.json", "y.json"],
                },
                "z": {
                    "label": "Z",
                    "capability": "cap_z",
                    "after": ["d"],
                    "outputs": ["z_out.json"],
                },
            },
        }
    )
    original = revisions.publish_workspace_revision(workspace["id"], old)
    # 新 revision：d 转为 start 入口（a/z 的入边改挂 d），x.json 改作 a 的
    # 外部输入（新图无生产者），y.json 彻底无人引用。
    new = workflow_definition_from_dict(
        {
            "key": "wfchain",
            "label": "Wf Chain",
            "nodes": {
                "d": {"label": "D", "type": "start"},
                "a": {
                    "label": "A",
                    "capability": "cap_a",
                    "after": ["d"],
                    "inputs": ["x.json"],
                },
                "z": {
                    "label": "Z",
                    "capability": "cap_z",
                    "after": ["d"],
                    "outputs": ["z_out.json"],
                },
            },
        }
    )
    current = revisions.publish_workspace_revision(workspace["id"], new)
    job = seed_inherit_job(queries, workspace, original, ["a", "d", "z"])
    # z 不变（入边签名同为 d→z）且身份可证明 + 产物可达 → 唯一继承节点。
    seed_impl_identity(queries, workspace, job["id"], ["z"])
    for key in ("a", "d", "z"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in ("x.json", "y.json", "z_out.json"):
        (job_dir / name).write_text(f"old-{name}")
    (job_dir / "runs" / "d").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "d" / "log.txt").write_text("history")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (("d", "x.json"), ("d", "y.json"), ("z", "z_out.json")):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job["id"], node_key, name, f"jobs/wschain/{job['id']}/{name}"),
            )
        d_request = _queued_request(conn, workspace["id"], job["id"], "d")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 1
    assert result["rerun_node_count"] == 1
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job["id"])}
    # d 的执行行随删除面消失；a 重置、z 继承。
    assert statuses == {"a": "pending", "z": "completed"}
    # y.json 三面退役；x.json 作为新图外部输入（种子）保留；z 原样。
    assert not (job_dir / "y.json").exists()
    assert (job_dir / "x.json").read_text() == "old-x.json"
    assert (job_dir / "z_out.json").read_text() == "old-z_out.json"
    names = queries.job_artifact_manifest_names_for_nodes(job["id"], {"d", "z"})
    assert names == {("d", "x.json"), ("z", "z_out.json")}
    # 运行历史目录与 queued 请求一并了结（A2/A4 同口径）。
    assert not (job_dir / "runs" / "d").exists()
    with closing(connect_database(queries.dsn_identity)) as conn:
        state = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (d_request,),
        ).fetchone()["state"]
    assert state == "cancelled"
    assert queries.get_job(job["id"])["workflow_revision_id"] == current["id"]


def test_inherit_upgrade_rmw_name_of_covered_consumer_is_retired(tmp_path: Path) -> None:
    """codex #776 R8 P1-A：判为 clean 的 RMW 附着名必须实际进删除面。

    p 纯产 x、q 是 p 显式下游的 RMW 节点（inputs/outputs 同 x），升级把
    两者都重置：保护计划凭 p→q 先行证据判 x 为 clean——但暂存面的 RMW
    排除（#114）与提交后 sweep 的 RMW 排除让旧 x 的本地文件与清单行都
    存活；p 重跑若没写 x，``_check_outputs`` 只查存在性就会把旧字节登记
    为 p 的新输出、q 消费旧 revision 的结果。修复：有先行顺序证明的
    clean RMW 名强制进事务内暂存 + 清单删除（并进提交后 sweep 面）。
    """
    import dataclasses

    from server.app.services.job_artifact_mutation import JobArtifactMutationService
    from server.app.storage_paths import resolve_job_dir

    queries, workspace, revisions, _, _ = setup_inherit_env(tmp_path)
    nodes = {
        "p": WorkflowNode(key="p", label="P", capability="cap_p", outputs=["x.json"]),
        "q": WorkflowNode(
            key="q",
            label="Q",
            capability="cap_q",
            after=["p"],
            inputs=["x.json"],
            outputs=["x.json"],
        ),
    }
    original = revisions.publish_workspace_revision(
        workspace["id"],
        WorkflowDefinition(key="wfchain", label="Wf Chain", intake=WorkflowIntake(), nodes=nodes),
    )
    # p 的 capability 变化 → p 与显式下游 q 都进重置面。
    changed = {
        **nodes,
        "p": dataclasses.replace(nodes["p"], capability="cap_p_new"),
    }
    revisions.publish_workspace_revision(
        workspace["id"],
        WorkflowDefinition(key="wfchain", label="Wf Chain", intake=WorkflowIntake(), nodes=changed),
    )
    job = seed_inherit_job(queries, workspace, original, ["p", "q"])
    for key in ("p", "q"):
        queries.update_job_node(job["id"], key, status="completed")
    queries.update_job_status(job["id"], "completed")
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_text("old-x")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
            values (%s, 'p', 'x.json', %s, 1, 'hash')
            """,
            (job["id"], f"jobs/wschain/{job['id']}/x.json"),
        )
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job["id"], mode="inherit")

    assert result["status"] == "succeeded"
    assert result["rerun_node_count"] == 2
    # clean 判定与删除面一致：旧 x 的本地文件与清单行都退役（p 重跑必须
    # 真写 x，否则响亮失败——不会把旧字节当新输出）。
    assert not (job_dir / "x.json").exists()
    assert queries.job_artifact_manifest_names_for_nodes(job["id"], {"p", "q"}) == set()
