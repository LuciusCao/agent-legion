"""codex 第三轮修复的 inherit 升级测试（issue #645，PR #702）。

从 ``test_job_workflow_upgrade_inherit.py`` 按轮次拆出的姊妹文件
（文件预算）：P1-1 事务内暂存（lease guard 竞争窗口）、P1-2 未完成
候选的遗留输出暂存、P1-3 共享纯输出名的闭包收敛（纯函数），
P2 旧配置 re-parse 失败的保守退化。
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_database
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_reset_closure import shared_name_rerun_closure
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL


def _outputs_chain(a_out: str = "a_out.json", b_out: str = "b_out.json") -> WorkflowDefinition:
    """a → b → c 三级链，a/b 声明 outputs（产物暂存场景的公共构造）。"""
    return WorkflowDefinition(
        key="wfchain",
        label="Wf Chain",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=[a_out]),
            "b": WorkflowNode(
                key="b",
                label="B",
                capability="cap_b",
                after=["a"],
                outputs=[b_out],
                config_schema={},
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
        },
    )


def _setup(tmp_path: Path, definition: WorkflowDefinition):
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("wschain", default_workflow_key="wfchain")
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], definition)
    return queries, workspace, revisions, original


def _seed_job(queries, workspace, original, node_keys) -> str:
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
    from server.app.services.job_workflow_upgrade_config import intake_frozen_config_json
    from server.app.workflows.definition import workflow_definition_from_dict

    definition = workflow_definition_from_dict(json.loads(original["definition_json"]))
    frozen = intake_frozen_config_json(queries, workspace["id"], definition)
    if frozen is not None:
        with closing(connect_database(queries.dsn_identity)) as conn, conn:
            conn.execute("update jobs set frozen_config_json=%s where id=%s", (frozen, job["id"]))
    return str(job["id"])


def _make_service(tmp_path: Path, queries: JobQueries) -> JobWorkflowUpgradeService:
    return JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )


def test_inherit_upgrade_stages_outputs_inside_lease_guard(tmp_path: Path, monkeypatch) -> None:
    """codex P1-1：产物暂存必须发生在 lease guard 事务内。

    竞争窗口模拟（test_run_to_atomic_guard_catches_lease_created_after_precheck
    同款）：DB 里先插入 active lease（调度器在 resolve_upgrade_context 检查
    后抢到 job），预检 ``has_active_for_job`` 被蒙蔽放行——guard 事务内的
    复检必须拦截。旧缺陷形态：暂存在 guard 事务外先行执行（文件被移进
    .staged 后 guard 才冲突回滚），窗口内执行中节点会读到缺失文件或写进
    暂存路径。修复对齐 rerun/run_to：暂存进 guard 事务内，冲突时
    stage_outputs 从未被调用、零文件移动。
    """
    import dataclasses

    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.storage_paths import resolve_job_dir

    definition = _outputs_chain()
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    # 新 revision 改 b 的 capability → b/c 重置、a 继承。
    changed = dataclasses.replace(definition.nodes["b"], capability="cap_b_new")
    revisions.publish_workspace_revision(
        workspace["id"],
        dataclasses.replace(definition, nodes={**definition.nodes, "b": changed}),
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")

    # 调度器抢到 job：节点 b 从 completed 之外的 pending 起步转 running
    # 并持有 active lease，但预检 has_active_for_job 被蒙蔽放行
    # （resolve_upgrade_context 窗口内竞争）。
    queries.update_job_node(job_id, "b", status="pending")
    run = queries.start_node_run(job_id, "b", ["pi"], "")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute(
            """
            insert into executor_leases(id, execution_id, executor_id, workspace_id,
              job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)
            values ('lease-race', 'exec-race', 'pi-race', %s, %s, 'b', %s, 'active',
              current_timestamp, current_timestamp, '2999-01-01')
            """,
            (workspace["id"], job_id, run["id"]),
        )
    lease_repo = ExecutorLeaseRepository(queries, data_dir=tmp_path)
    monkeypatch.setattr(lease_repo, "has_active_for_job", lambda _job_id, _now: False)

    stage_calls: list[bool] = []
    real_stage = JobArtifactMutationService.stage_outputs

    def probing_stage(self, job, node_keys, definition, **kwargs):
        stage_calls.append(True)
        return real_stage(self, job, node_keys, definition, **kwargs)

    monkeypatch.setattr(JobArtifactMutationService, "stage_outputs", probing_stage)
    service = JobWorkflowUpgradeService(
        queries,
        lease_repo,
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # guard 事务复检拦下竞争 lease：升级 skipped，暂存从未发生（旧缺陷
    # 形态下 stage_outputs 在 guard 之前被调、文件被移动后回滚），
    # 文件原位、DB 未动。
    assert result["status"] == "skipped"
    assert result["reason_code"] == "busy"
    assert stage_calls == []
    assert (job_dir / "a_out.json").read_text() == "old-a"
    assert (job_dir / "b_out.json").read_text() == "old-b"
    assert not (job_dir / ".staged").exists()
    assert queries.get_job(job_id)["workflow_revision_id"] == original["id"]


def test_inherit_upgrade_stages_leftover_outputs_of_uncompleted_candidates(
    tmp_path: Path,
) -> None:
    """codex P1-2：未完成候选的遗留输出必须进暂存面。

    旧缺陷：暂存范围 = 全部节点 − diff 候选集，但 mutation 只保留候选
    中 completed 的——failed/pending 候选会被重置 pending，其上次失败/
    中断遗留的输出文件不在暂存面：旧文件和清单行残留，executor 的输出
    存在性检查把半成品当作本次有效输出。修复：暂存面按事务内实际保留集
    （候选 ∩ completed）计算重置面。
    """
    from server.app.storage_paths import resolve_job_dir

    definition = _outputs_chain()
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    # 新 revision 与旧定义完全一致（无变更）→ a/b 都是 diff 候选；但 a
    # 处于 failed（上次运行写了一半 a_out.json 就崩了），b completed。
    revisions.publish_workspace_revision(workspace["id"], definition)
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    # a/b 的实现身份均可证明（P1-1）：a 播种后再置 failed——无记录的
    # completed 会被保守排除并沿下游闭包把 b 连带重置，遮蔽本用例的
    # 「未完成候选遗留输出暂存」判别点。a 保持 failed 无产物。
    from tests.helpers.job_workflow_upgrade import seed_impl_identity as _seed_impl_identity

    _seed_impl_identity(queries, workspace, job_id, ["a", "b"])
    queries.update_job_node(job_id, "a", status="failed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("partial-a")
    (job_dir / "b_out.json").write_text("old-b")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key, name in (("a", "a_out.json"), ("b", "b_out.json")):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, %s, %s, 1, 'hash')
                """,
                (job_id, node_key, name, f"jobs/wschain/{job_id}/{name}"),
            )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # a 未完成 → 作为事务内新 reset 种子；统一闭包必须把其 completed
    # 下游 b 一并重置，否则 b 会继续保留基于旧 a 结果的产物。a 的半成品
    # 与 b 的旧下游产物都进暂存面，不会骗过 executor 的存在性检查。
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    assert not (job_dir / "a_out.json").exists()
    assert not (job_dir / "b_out.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b"})
    assert names == set()
    assert not (job_dir / ".staged").exists()


def test_shared_name_rerun_closure_cascades_to_downstream() -> None:
    """codex P1-3 纯函数：共享名排除含下游闭包，独立节点不受波及。

    链 a → b → c，x 与 a 同为 out.json 生产者、y 独立：keep={a,b,y}、
    reset_face={x,c} 时——a 与 x 共享 out.json → a 排除；a 的下游 b
    （a 重跑后旧产物语义已替换）一并排除；y 无共享名不受波及。
    """
    definition = WorkflowDefinition(
        key="wf",
        label="Wf",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["out.json"]),
            "b": WorkflowNode(key="b", label="B", capability="cap_b", after=["a"]),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["b"]),
            "x": WorkflowNode(key="x", label="X", capability="cap_x", outputs=["out.json"]),
            "y": WorkflowNode(key="y", label="Y", capability="cap_y", outputs=["y.json"]),
        },
    )

    excluded = shared_name_rerun_closure(definition, frozenset({"a", "b", "y"}), {"x", "c"})

    assert excluded == {"a", "b"}
    # 对照：keep 侧无共享名时零排除。
    assert shared_name_rerun_closure(definition, frozenset({"y"}), {"x", "c"}) == set()
    # 对照：reset 面没有纯输出名时零排除。
    assert shared_name_rerun_closure(definition, frozenset({"a", "b"}), {"c"}) == set()


def test_inherit_upgrade_null_frozen_old_schema_conflict_degrades_clean(
    tmp_path: Path,
) -> None:
    """codex P2：NULL frozen 且旧定义按当前配置解析失败 → 降级 clean，不 500。

    场景：legacy job（frozen NULL）的旧快照节点声明 enum 约束，生产后
    workspace override 换成 enum 外的值——当前 override 对新 revision 有效
    （新快照没有该约束），但在旧定义上 re-parse 抛 ValueError。旧行为：
    plan_inherit_nodes 不捕获 → upgrade() 500、batch 中断；修复：与
    「旧侧基准不可证明 → 保守退化」语义一致，捕获后全量重跑。
    """
    import dataclasses

    from server.app.storage_paths import resolve_job_dir

    old_schema = {
        "type": "object",
        "properties": {"bank_version": {"type": "string", "enum": ["v1", "v2"]}},
    }
    base = _outputs_chain(a_out="a_out.json", b_out="b_out.json")
    old_nodes = {
        "a": dataclasses.replace(base.nodes["a"], config_schema=old_schema),
        "b": base.nodes["b"],
        "c": base.nodes["c"],
    }
    old_definition = dataclasses.replace(base, nodes=old_nodes)
    queries, workspace, revisions, original = _setup(tmp_path, old_definition)
    # 新 revision 放开 enum 约束：当前 override（v9）对新定义合法。
    new_nodes = dict(old_nodes)
    new_nodes["a"] = dataclasses.replace(
        old_nodes["a"],
        config_schema={"type": "object", "properties": {"bank_version": {"type": "string"}}},
    )
    current = revisions.publish_workspace_revision(
        workspace["id"], dataclasses.replace(base, nodes=new_nodes)
    )
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="completed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    # legacy 存量行：intake 冻结值 NULL + workspace override 是旧 enum 外的值。
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        conn.execute("update jobs set frozen_config_json=null where id=%s", (job_id,))
    queries.update_workspace(
        workspace["id"], node_config={"wfchain": {"a": {"bank_version": "v9"}}}
    )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    # 旧定义按当前配置 re-parse 抛 ValueError → 捕获降级：升级成功、
    # 全量重跑（不再让单 job 500 / batch 中断），job 落到新 revision。
    assert result["status"] == "succeeded"
    assert result["kept_node_count"] == 0
    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    assert set(statuses.values()) == {"pending"}
    upgraded = queries.get_job(job_id)
    assert upgraded["workflow_revision_id"] == current["id"]
    assert json.loads(upgraded["frozen_config_json"])["a"]["bank_version"] == "v9"


def test_failed_candidate_shared_name_face_expansion_keeps_inherited_data_safe(
    tmp_path: Path,
) -> None:
    """M1（codex 三轮复审）：事务内复算层的独有价值场景。

    同 revision 升级（无 diff 变更）+ a(completed) 与 b(failed) 都声明
    shared.json 时，plan 阶段的共享名排除看不到 b（b 不在 diff 候选的
    排除语境里——它本来就"无变更"）；b 因未完成并入重置面后，事务内
    按实际保留集复算才发现共享名 face 扩张，把 a 一并排除——单删事务
    内层会让 b 重跑覆盖 shared.json 而 a 的清单行指向半成品字节
    （数据串写）。此场景由事务内复算层独立防御，单删该层无其他报警。
    """
    from server.app.storage_paths import resolve_job_dir

    # a 与 b 都声明 shared.json（同 revision；b 上次失败留下半成品）。
    definition = _outputs_chain(a_out="shared.json", b_out="shared.json")
    queries, workspace, revisions, original = _setup(tmp_path, definition)
    revisions.publish_workspace_revision(workspace["id"], definition)
    job_id = _seed_job(queries, workspace, original, ["a", "b", "c"])
    queries.update_job_node(job_id, "a", status="completed")
    queries.update_job_node(job_id, "b", status="failed")
    job = queries.get_job(job_id)
    job_dir = resolve_job_dir(job, queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    # b 失败时写了一半的 shared.json——物理上与 a 的产物同名共存。
    (job_dir / "shared.json").write_text("partial-from-b")
    with closing(connect_database(queries.dsn_identity)) as conn, conn:
        for node_key in ("a", "b"):
            conn.execute(
                """
                insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)
                values (%s, %s, 'shared.json', %s, 1, 'hash')
                """,
                (job_id, node_key, f"jobs/wschain/{job_id}/shared.json"),
            )
    service = _make_service(tmp_path, queries)

    result = service.upgrade(workspace["id"], job_id, mode="inherit")

    statuses = {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}
    # 事务内复算层发现共享名 face 扩张：a 不能按"无变更 completed"继承
    # （它的 shared.json 与 b 的重置面冲突），与 b 一起重跑。
    assert result["kept_node_count"] == 0
    assert statuses == {"a": "pending", "b": "pending", "c": "pending"}
    # 文件进暂存面（清单行同集合删除），不会以 a 的名义指向 b 的半成品。
    assert not (job_dir / "shared.json").exists()
    names = queries.job_artifact_manifest_names_for_nodes(job_id, {"a", "b"})
    assert names == set()
