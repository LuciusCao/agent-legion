"""clean 升级的暂存与清单删除行为（#759 预算拆分自 ``test_job_workflow_upgrade``）。

核心不变式：旧产物名的存亡由输入保护计划（``job_workflow_upgrade_protection``
的 keep 集）与 removed 面（``job_workflow_upgrade_removed_outputs``）唯一判定，
「保留 ⇔ 未暂存」构造性成立；被删节点的产物名进 removed 面、run history 经
``extra_run_keys`` 一并暂存。
"""

from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL


def _two_revision_env(queries, tmp_path, old_nodes, new_nodes):
    """旧/新两个自定义 revision：job 钉在旧 revision 上，active 已是新 revision。"""
    workspace = queries.create_workspace("ws-upg", default_workflow_key="wf_upg")
    old_def = WorkflowDefinition(
        key="wf_upg", label="wf_upg", intake=WorkflowIntake(), nodes=old_nodes
    )
    new_def = WorkflowDefinition(
        key="wf_upg", label="wf_upg", intake=WorkflowIntake(), nodes=new_nodes
    )
    revisions = WorkflowRevisionService(queries)
    original = revisions.publish_workspace_revision(workspace["id"], old_def)
    revisions.publish_workspace_revision(workspace["id"], new_def)
    job = queries.create_job(
        workflow_key="wf_upg",
        source_type="question",
        source_id="Q1",
        run_id="batch1",
        title="Q1",
        node_keys=[k for k in old_nodes if k != "_start"],
        workspace_id=workspace["id"],
        workflow_revision_id=original["id"],
        workflow_version=original["version"],
        workflow_definition_hash=original["definition_hash"],
        workflow_definition_snapshot_json=original["definition_json"],
    )
    queries.update_job_status(job["id"], "completed")
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
    )
    return workspace, job, service


def test_upgrade_deletes_shard_rows(tmp_path: Path) -> None:
    """#759 自审 P1：clean 升级必须删除 node_shards——分片行只 FK 到 jobs，
    不随 job_nodes 级联；残留旧行会让新 revision 同名分片节点沿用上一轮
    input_json/状态。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {"k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"])},
        {"k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"])},
    )
    with queries.connect() as conn:
        conn.execute(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values (%s, 'k', 0, 'completed', '{}')",
            (job["id"],),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    with queries.connect() as conn:
        rows = conn.execute(
            "select shard_index from node_shards where job_id=%s", (job["id"],)
        ).fetchall()
    assert rows == []


def test_upgrade_stages_dropped_outputs_and_preserves_rmw_rows(tmp_path: Path) -> None:
    """#759 自审：共有节点被新 revision 删掉的 output 一并失效；RMW 名的
    清单行保留（rerun/run-to 对 RMW 三者全保留，升级不得留本地单副本）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {
            "k": WorkflowNode(
                key="k",
                label="K",
                capability="c",
                inputs=["rmw.json"],
                outputs=["a.json", "b.json", "rmw.json", "seed.json"],
            )
        },
        {
            "k": WorkflowNode(
                key="k",
                label="K",
                capability="c",
                inputs=["rmw.json", "seed.json"],
                outputs=["a.json", "rmw.json"],
            )
        },
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "b.json").write_text("stale", encoding="utf-8")
    (job_dir / "rmw.json").write_text("seed", encoding="utf-8")
    (job_dir / "seed.json").write_text("seed2", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values"
            " (%s, 'k', 'b.json', 'k/b.json', 1, ''),"
            " (%s, 'k', 'rmw.json', 'k/rmw.json', 1, ''),"
            " (%s, 'k', 'seed.json', 'k/seed.json', 1, '')",
            (job["id"], job["id"], job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert not (job_dir / "b.json").exists()
    assert (job_dir / "rmw.json").read_text(encoding="utf-8") == "seed"
    assert (job_dir / "seed.json").read_text(encoding="utf-8") == "seed2"
    with queries.connect() as conn:
        remaining = {
            row["name"]
            for row in conn.execute(
                "select name from job_artifacts where job_id=%s", (job["id"],)
            ).fetchall()
        }
    # RMW 名与 output→input 转移名（seed.json：旧 output、新纯 input）的
    # 清单行都必须保留——本地文件只是可淘汰缓存，权威副本删了输入不可
    # 恢复（#759 codex P1）。
    assert remaining == {"rmw.json", "seed.json"}


def test_upgrade_staging_failure_leaves_no_partial_state(tmp_path: Path, monkeypatch) -> None:
    """#759 自审 P1：暂存失败时 DB 与文件都必须零半程——不允许 DB 未变而
    产物滞留在 .staged。（单次 stage_outputs 内部的部分移动回滚由
    test_job_artifact_mutation 的 partial-move 用例钉住；本用例钉服务层
    失败臂：异常原样上抛、作业状态不变、无暂存残留。）"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {
            "k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"]),
            "old_only": WorkflowNode(
                key="old_only", label="Old", capability="c", outputs=["z.json"]
            ),
        },
        {"k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"])},
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a.json").write_text("a", encoding="utf-8")

    def failing_stage(self, job_arg, keys, definition, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(JobArtifactMutationService, "stage_outputs", failing_stage)

    import pytest as _pytest

    with _pytest.raises(OSError, match="disk failure"):
        service.upgrade(workspace["id"], job["id"])

    assert (job_dir / "a.json").read_text(encoding="utf-8") == "a"
    assert not (job_dir / ".staged").exists()
    assert queries.get_job(job["id"])["status"] == "completed"


def test_upgrade_preserves_cross_node_seed_and_stages_removed_rmw(tmp_path: Path) -> None:
    """#759 自审：跨节点 output→input 转移（旧 k 产出、新 e 消费、新定义
    无生产者）的种子三平面全保留；被删节点且新定义无人消费的 RMW 名
    按名闭包失效（死名 ⊆ dropped，第一段 extra_names 覆盖）。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {
            "k": WorkflowNode(key="k", label="K", capability="c", outputs=["x.json"]),
            "e": WorkflowNode(
                key="e", label="E", capability="c2", inputs=["x.json"], outputs=["y.json"]
            ),
            "d": WorkflowNode(
                key="d",
                label="D",
                capability="c3",
                inputs=["z.json"],
                outputs=["z.json"],
            ),
        },
        {
            "k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"]),
            "e": WorkflowNode(
                key="e", label="E", capability="c2", inputs=["x.json"], outputs=["y.json"]
            ),
        },
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "x.json").write_text("seed", encoding="utf-8")
    (job_dir / "z.json").write_text("rmw", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values"
            " (%s, 'k', 'x.json', 'k/x.json', 1, ''),"
            " (%s, 'd', 'z.json', 'k/z.json', 1, '')",
            (job["id"], job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert (job_dir / "x.json").read_text(encoding="utf-8") == "seed"
    assert not (job_dir / "z.json").exists()
    with queries.connect() as conn:
        remaining = {
            row["name"]
            for row in conn.execute(
                "select name from job_artifacts where job_id=%s", (job["id"],)
            ).fetchall()
        }
    assert remaining == {"x.json"}


def test_upgrade_preserves_seed_from_removed_producer(tmp_path: Path) -> None:
    """#759 codex P1：生产者节点被新 revision 整体删除、其 output 转移为
    新节点的纯 input（含被删节点的 RMW 名变成新输入）时，种子三平面全
    保留——第二段只清 run history，不得按旧定义重枚举被删节点 outputs。
    死名（无人消费）仍按名闭包失效。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {
            "p": WorkflowNode(key="p", label="P", capability="c", outputs=["x.json", "dead.json"]),
            "d": WorkflowNode(
                key="d",
                label="D",
                capability="c3",
                inputs=["z.json"],
                outputs=["z.json"],
            ),
            "q": WorkflowNode(
                key="q", label="Q", capability="c2", inputs=["x.json"], outputs=["y.json"]
            ),
        },
        {
            "q": WorkflowNode(
                key="q",
                label="Q",
                capability="c2",
                inputs=["x.json", "z.json"],
                outputs=["y.json"],
            )
        },
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    (job_dir / "runs" / "p").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "p" / "1.json").write_text("{}", encoding="utf-8")
    (job_dir / "x.json").write_text("seed-x", encoding="utf-8")
    (job_dir / "z.json").write_text("seed-z", encoding="utf-8")
    (job_dir / "dead.json").write_text("stale", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values"
            " (%s, 'p', 'x.json', 'p/x.json', 1, ''),"
            " (%s, 'd', 'z.json', 'd/z.json', 1, ''),"
            " (%s, 'p', 'dead.json', 'p/dead.json', 1, '')",
            (job["id"], job["id"], job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    # 种子三平面保留：本地文件与清单行；被删节点的 run history 清除。
    assert (job_dir / "x.json").read_text(encoding="utf-8") == "seed-x"
    assert (job_dir / "z.json").read_text(encoding="utf-8") == "seed-z"
    assert not (job_dir / "dead.json").exists()
    assert not (job_dir / "runs" / "p").exists()
    with queries.connect() as conn:
        remaining = {
            row["name"]
            for row in conn.execute(
                "select name from job_artifacts where job_id=%s", (job["id"],)
            ).fetchall()
        }
    assert remaining == {"x.json", "z.json"}


def test_upgrade_rolls_back_staged_outputs_when_db_mutation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """review P2：暂存成功后 DB 突变失败——apply 的补偿臂必须把暂存件回滚
    （文件回原位、DB 无半程）。暂存自身失败的补偿由
    test_upgrade_staging_failure_leaves_no_partial_state 覆盖，本用例钉住
    暂存成功、mutation 抛错这一臂。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {
            "k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"]),
            "old_only": WorkflowNode(
                key="old_only", label="Old", capability="c", outputs=["z.json"]
            ),
        },
        {"k": WorkflowNode(key="k", label="K", capability="c", outputs=["a.json"])},
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    (job_dir / "runs" / "old_only").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs" / "old_only" / "1.json").write_text("{}", encoding="utf-8")
    (job_dir / "a.json").write_text("a", encoding="utf-8")
    (job_dir / "z.json").write_text("z", encoding="utf-8")

    from server.app.services import job_workflow_upgrade_apply as apply_module

    def boom(*_args, **_kwargs):
        raise OSError("db boom")

    monkeypatch.setattr(apply_module, "upgrade_job_workflow_inherit", boom)

    import pytest as _pytest

    with _pytest.raises(OSError, match="db boom"):
        service.upgrade(workspace["id"], job["id"])

    assert (job_dir / "a.json").read_text(encoding="utf-8") == "a"
    assert (job_dir / "z.json").read_text(encoding="utf-8") == "z"
    assert (job_dir / "runs" / "old_only" / "1.json").read_text(encoding="utf-8") == "{}"
    assert queries.get_job(job["id"])["status"] == "completed"


def test_upgrade_preserves_removed_output_as_new_rmw_seed(tmp_path: Path) -> None:
    """review P2 同族：被删生产者的 output 变成新节点的 RMW 名（同名
    input+output）——按名闭包判活，三者全保留作首轮 RMW 的种子。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, service = _two_revision_env(
        queries,
        tmp_path,
        {"p": WorkflowNode(key="p", label="P", capability="c", outputs=["w.json"])},
        {
            "q": WorkflowNode(
                key="q",
                label="Q",
                capability="c2",
                inputs=["w.json"],
                outputs=["w.json"],
            )
        },
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "w.json").write_text("seed-w", encoding="utf-8")
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'p', 'w.json', 'p/w.json', 1, '')",
            (job["id"],),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert (job_dir / "w.json").read_text(encoding="utf-8") == "seed-w"
    with queries.connect() as conn:
        remaining = {
            row["name"]
            for row in conn.execute(
                "select name from job_artifacts where job_id=%s", (job["id"],)
            ).fetchall()
        }
    assert remaining == {"w.json"}


def test_upgrade_deletes_objects_only_for_staged_names(tmp_path: Path) -> None:
    """review P2：对象存储删除严格由被删清单行驱动——种子名的对象绝不进
    delete_objects；被暂存名（死名 + 新节点将重写的 output）的对象被删。"""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace, job, _service = _two_revision_env(
        queries,
        tmp_path,
        {
            "p": WorkflowNode(key="p", label="P", capability="c", outputs=["x.json", "dead.json"]),
            "q": WorkflowNode(
                key="q", label="Q", capability="c2", inputs=["x.json"], outputs=["y.json"]
            ),
        },
        {
            "q": WorkflowNode(
                key="q", label="Q", capability="c2", inputs=["x.json"], outputs=["y.json"]
            )
        },
    )

    class _RecordingStore:
        enabled = True

        def __init__(self) -> None:
            self.deleted_keys: list[str] = []

        def delete_objects(self, rows) -> None:
            self.deleted_keys.extend(str(row["storage_key"]) for row in rows)

    store = _RecordingStore()
    service = JobWorkflowUpgradeService(
        queries,
        ExecutorLeaseRepository(queries, data_dir=tmp_path),
        artifact_mutation=JobArtifactMutationService(queries.jobs_dir),
        object_store=store,
    )
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    job_dir.mkdir(parents=True, exist_ok=True)
    with queries.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values"
            " (%s, 'p', 'x.json', 'p/x.json', 1, ''),"
            " (%s, 'p', 'dead.json', 'p/dead.json', 1, ''),"
            " (%s, 'q', 'y.json', 'q/y.json', 1, '')",
            (job["id"], job["id"], job["id"]),
        )

    result = service.upgrade(workspace["id"], job["id"])

    assert result["status"] == "succeeded"
    assert sorted(store.deleted_keys) == ["p/dead.json", "q/y.json"]
