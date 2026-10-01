"""Regression tests for #508: rerun must delete the affected nodes'
``job_artifacts`` manifest rows in the same transaction as the node reset.

Before the fix, rerun cleaned only the local job_dir (stage_outputs) while
the object-storage manifest rows survived, so a rerun that never completed
again left the job listing — and serving — the PREVIOUS run's artifacts
(``names_for_job`` unions the manifest; single-artifact reads fall back to
S3). Three entry points share the semantics: single rerun, run-to, and
approval rework. RMW artifacts (input ∩ output of the same node) are
excluded, mirroring stage_outputs (#114). 暂存闭包口径（隐式消费者 /
同名生产者收敛）的用例见姊妹文件
``test_job_rerun_manifest_gc_closure.py``。
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.approval_decisions import ApprovalDecisionService
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revision_format import serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    workflow_definition_from_mapping,
)
from tests.fakes.storage import FakeObjectStorage

pytestmark = pytest.mark.postgres


@pytest.fixture
def chain_definition():
    return WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "up": WorkflowNode(key="up", label="Up", capability="up", outputs=["up.json"]),
            "down": WorkflowNode(
                key="down",
                label="Down",
                capability="down",
                after=["up"],
                inputs=["up.json"],
                outputs=["down.json"],
            ),
        },
    )


def _seed_job_with_manifest(
    job_db: Any,
    settings: Any,
    definition: WorkflowDefinition,
    *,
    workspace: Any,
    storage: FakeObjectStorage,
    source_id: str = "Q1",
    node_outputs: tuple[tuple[str, str], ...] = (("up", "up.json"), ("down", "down.json")),
) -> dict[str, Any]:
    node_keys = [key for key, _ in node_outputs]
    batch = job_db.create_run(
        definition.key,
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id=source_id,
        run_id=batch["id"],
        title="Question 1",
        node_keys=node_keys,
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    for key in node_keys:
        job_db.update_job_node(job["id"], key, status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    store = JobArtifactObjectStore(job_db, storage)
    for node_key, name in node_outputs:
        (storage_dir / name).write_text(f"{name} content")
        store.upload(
            workspace_id=str(workspace["id"]),
            job_id=job["id"],
            node_key=node_key,
            name=name,
            local_path=storage_dir / name,
        )
    return job


def _make_rerun_service(job_db, settings, storage) -> JobRerunService:
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )


def test_rerun_deletes_downstream_manifest_rows_and_objects(job_db, settings, chain_definition):
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "up")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    # Both rows gone: the rerun target's and the downstream closure's — the
    # job must stop listing (and serving) the previous run's artifacts.
    assert store.names_for_job(job["id"]) == set()
    # The objects themselves were best-effort deleted post-commit (the fake
    # removes deleted keys from ``objects`` and records them in ``deleted``).
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"up.json", "down.json"}


def test_rerun_keeps_unaffected_nodes_manifest_rows(job_db, settings, chain_definition):
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    # Rerun the DOWNSTREAM node: "up" is upstream, untouched.
    result = service.rerun(workspace["id"], job["id"], "down")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == {"up.json"}


def test_rerun_keeps_rmw_manifest_rows(job_db, settings):
    definition = WorkflowDefinition(
        key="rmw_workflow",
        label="RMW",
        intake=WorkflowIntake(),
        nodes={
            "publish": WorkflowNode(
                key="publish",
                label="Publish",
                capability="publish",
                outputs=["result.json", "manifest.json"],
            ),
        },
    )
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="rmw_workflow")
    batch = job_db.create_run(
        "rmw_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="rmw_workflow",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["publish"],
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    job_db.update_job_node(job["id"], "publish", status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    store = JobArtifactObjectStore(job_db, storage)
    for name in ("result.json", "manifest.json"):
        (storage_dir / name).write_text(f"{name} content")
        store.upload(
            workspace_id=str(workspace["id"]),
            job_id=job["id"],
            node_key="publish",
            name=name,
            local_path=storage_dir / name,
        )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "publish")

    assert result["status"] == "succeeded"
    # Both files are RMW candidates only if declared as inputs; here
    # manifest.json is a pure output — assert the pure output row is gone
    # while any RMW-named row (declared as input AND output elsewhere in the
    # closure) survives. With no inputs declared, all rows go.
    assert store.names_for_job(job["id"]) == set()


def test_run_to_deletes_start_closure_manifest_rows(job_db, settings, chain_definition):
    """The run-to entry point shares the manifest GC: staging the start
    node's closure must remove those rows too."""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )

    result = service.run_to(workspace["id"], job["id"], "down", start_node_key="up")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"up.json", "down.json"}


def test_approval_rework_deletes_target_closure_manifest_rows(job_db, settings):
    """The approval rework entry point shares the manifest GC: deciding a
    rework on the gate resets its rework_target ("write") and the downstream
    closure — those manifest rows and objects must go."""
    dag = {
        "key": "approval_gc",
        "label": "Approval GC",
        "schema_version": 2,
        "nodes": {
            "entry": {"type": "start", "label": "入口"},
            "write": {"label": "写稿", "capability": "write", "outputs": ["script.md"]},
            "gate": {
                "type": "approval",
                "label": "审批",
                "inputs": ["script.md"],
                "config": {"rework_target": "write"},
            },
        },
        "edges": [
            {"from": "entry", "to": "write"},
            {"from": "write", "to": "gate"},
        ],
    }
    definition = workflow_definition_from_mapping(dag)
    workspace = job_db.create_workspace(name="approval-gc-ws", default_workflow_key="approval_gc")
    WorkflowRevisionService(job_db).ensure_active_revision(str(workspace["id"]), definition)
    job = job_db.create_job(
        workflow_key="approval_gc",
        source_type="material",
        source_id="chapter-1",
        run_id="",
        title="第一章",
        node_keys=list(definition.executable_nodes),
        workspace_id=str(workspace["id"]),
    )
    storage = FakeObjectStorage()
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "script.md").write_text("draft", encoding="utf-8")
    store = JobArtifactObjectStore(job_db, storage)
    store.upload(
        workspace_id=str(workspace["id"]),
        job_id=job["id"],
        node_key="write",
        name="script.md",
        local_path=storage_dir / "script.md",
    )
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='write'",
            (job["id"],),
        )
        conn.execute(
            "update job_nodes set status='awaiting_approval' where job_id=%s and node_key='gate'",
            (job["id"],),
        )
    rerun = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
        object_store=store,
    )
    service = ApprovalDecisionService(job_db, settings, rerun, object_store=store)

    service.decide(
        str(workspace["id"]),
        job["id"],
        "gate",
        verdict="rework",
        note="redo",
        decided_by="user:u1",
    )

    # The old run's artifact row and object are gone; the rework decision
    # then uploads its own feedback artifact for the fresh attempt.
    assert store.names_for_job(job["id"]) == {"review_feedback.json"}
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"script.md"}


def test_rerun_object_cleanup_spares_re_registered_authority_keys(
    job_db, settings, chain_definition
):
    """#508 review P1：事务提交与对象清理之间，job 已可再次调度——若新
    attempt 已完成并登记同一稳定权威键（jobs/{ws}/{job}/{name}），按旧
    快照删该键会让新清单行指向不存在的对象。清理前按当前清单重验：
    同键复现 = 新 attempt 的权威副本，跳过删除。"""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "up")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()  # 旧 run 行已删

    # 新 attempt 在清理运行前完成：重新登记同名产物的权威键（模拟
    # promote_all 的 server-side copy + record_remote_many 已提交）。
    up_key = f"jobs/{workspace['id']}/{job['id']}/up.json"
    down_key = f"jobs/{workspace['id']}/{job['id']}/down.json"
    storage.objects[up_key] = b"new run bytes"
    store.record_remote(
        workspace_id=str(workspace["id"]),
        job_id=job["id"],
        node_key="up",
        name="up.json",
        storage_key=up_key,
        size_bytes=13,
        content_hash="hash-new",
    )

    # 迟到的清理（拿的是事务时的旧行快照）：按当前清单重验后跳过
    # 已复现的键——新 attempt 的对象与清单行都完好。
    from server.app.services.job_staged_cleanup import delete_rerun_artifact_objects

    stale_rows = [
        {"node_key": "up", "name": "up.json", "storage_key": up_key},
        # 另一个未复现的键：照删（真孤儿，bucket lifecycle 的等价物）。
        {"node_key": "down", "name": "down.json", "storage_key": down_key},
    ]
    stale_call_baseline = len(storage.deleted)
    delete_rerun_artifact_objects(store, stale_rows, job["id"], "rerun")

    # The stale cleanup call itself must spare the re-registered key: count
    # its deletions via the isolated call (the earlier entries in
    # storage.deleted are service.rerun's own legitimate cleanup of the OLD
    # run's bytes). The object and its fresh manifest row both survive.
    deleted_by_stale_call = storage.deleted[stale_call_baseline:]
    assert up_key not in deleted_by_stale_call, "stale snapshot must not delete the fresh key"
    assert down_key in deleted_by_stale_call, "unre-registered orphan keys still delete"
    assert up_key in storage.objects
    assert store.lookup(job["id"], "up.json") is not None


def test_rerun_object_cleanup_revalidates_per_object_mid_delete(
    job_db, settings, chain_definition, monkeypatch
):
    """#683 review P1：批量探测与删除之间，新 attempt 的 promote_all 完成
    （权威键对象先拷、清单行 record_remote_many 后提交）——入口批量重验看
    不到它（读到的是旧状态），逐对象删除前的当前清单重验必须放过该键，
    否则新清单行指向被删对象。用真实 store + FakeObjectStorage 走完整删除
    路径，时序经 live_keys_for 靶向探针注入（#706 review P2：重验不传输
    整份清单）。"""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    store = JobArtifactObjectStore(job_db, storage)
    up_key = f"jobs/{workspace['id']}/{job['id']}/up.json"
    down_key = f"jobs/{workspace['id']}/{job['id']}/down.json"
    # 模拟已提交的 rerun 事务：受影响闭包的清单行已删，快照即 deleted_rows。
    with job_db.connect() as conn:
        conn.execute("delete from job_artifacts where job_id=%s", (job["id"],))

    real_live_keys_for = store.live_keys_for
    probes = {"count": 0}

    def live_keys_for_with_mid_cleanup_promote(job_id: str, storage_keys: list[str]) -> set[str]:
        probes["count"] += 1
        if probes["count"] == 2:
            # 批量探测（第 1 次）之后、up 的逐对象重验（第 2 次）之前：
            # 新 attempt 完成 promote_all——对象 copy 到同一权威键 +
            # record_remote_many 一个事务提交新清单行。
            storage.objects[up_key] = b"fresh attempt bytes!"
            store.record_remote(
                workspace_id=str(workspace["id"]),
                job_id=job["id"],
                node_key="up",
                name="up.json",
                storage_key=up_key,
                size_bytes=20,
                content_hash="hash-fresh",
            )
        return real_live_keys_for(job_id, storage_keys)

    monkeypatch.setattr(store, "live_keys_for", live_keys_for_with_mid_cleanup_promote)

    from server.app.services.job_staged_cleanup import delete_rerun_artifact_objects

    delete_rerun_artifact_objects(
        store,
        [
            {"node_key": "up", "name": "up.json", "storage_key": up_key},
            {"node_key": "down", "name": "down.json", "storage_key": down_key},
        ],
        job["id"],
        "rerun",
    )

    # 新 attempt 的对象与清单行都完好；未复现的孤儿键照删。
    assert up_key in storage.objects, "fresh authority object must survive"
    assert store.lookup(job["id"], "up.json") is not None
    assert up_key not in storage.deleted
    assert down_key in storage.deleted


def _boom_live_keys(job_id: str, storage_keys: list[str]) -> set[str]:
    raise RuntimeError("manifest probe boom")


def test_rerun_post_commit_cleanup_failure_still_succeeds(
    job_db, settings, chain_definition, monkeypatch
):
    """#759 P1：rerun 的 post-commit 对象清理抛错不得反转已提交的重置——
    结果仍 succeeded，清单行（事务内删除）保持已删。突变自检锚点：无兜底
    的实现会让 RuntimeError 冒出 rerun()，本用例变红。"""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)
    monkeypatch.setattr(service.object_store, "live_keys_for", _boom_live_keys)

    result = service.rerun(workspace["id"], job["id"], "up")

    assert result["status"] == "succeeded"
    assert JobArtifactObjectStore(job_db, storage).names_for_job(job["id"]) == set()
    nodes = {n["node_key"]: n["status"] for n in job_db.list_job_nodes(job["id"])}
    assert nodes["up"] == "pending"


def test_run_to_post_commit_cleanup_failure_still_succeeds(
    job_db, settings, chain_definition, monkeypatch
):
    """#759 P1：run-to 共享同一 post-commit 清理，抛错同样不反转结果。"""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    store = JobArtifactObjectStore(job_db, storage)
    monkeypatch.setattr(store, "live_keys_for", _boom_live_keys)
    service = JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        object_store=store,
    )

    result = service.run_to(workspace["id"], job["id"], "down", start_node_key="up")

    assert result["status"] == "succeeded"
    assert JobArtifactObjectStore(job_db, storage).names_for_job(job["id"]) == set()


def test_batch_rerun_continues_when_post_commit_cleanup_fails(
    job_db, settings, chain_definition, monkeypatch
):
    """#759 P1：批量 rerun 的每个 job 共享同一清理兜底——清理抛错不进入
    per-job 结果，也不中断整批（两个 job 都 succeeded）。"""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job_a = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    job_b = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage, source_id="Q2"
    )
    service = _make_rerun_service(job_db, settings, storage)
    monkeypatch.setattr(service.object_store, "live_keys_for", _boom_live_keys)

    results = service.batch_rerun(workspace["id"], [job_a["id"], job_b["id"]], "up")

    assert [r["job_id"] for r in results] == [job_a["id"], job_b["id"]]
    assert [r["status"] for r in results] == ["succeeded", "succeeded"]


def test_object_cleanup_skips_key_while_promote_holds_authority_lock(
    job_db, settings, chain_definition
):
    """codex #776 R7 P2-A：cleanup 与在途 promote 共享 artifact-authority 锁。

    在途 promote 已把新字节 copy 到稳定 authority key、但尚未登记清单行
    （同一事务的锁内后段）时，cleanup 的清单探针看不到行——无锁的清理会
    把刚写入的新对象删掉，随后 promote 登记留下悬挂行。修复后 cleanup
    的删除在该 key 的锁被持有时跳过（保守方向：旧对象成孤儿由 bucket
    lifecycle 兜底，绝不误删新字节）；锁释放后（promote 已提交，行可见）
    复核命中存活行同样跳过。本用例用另一连接真实持有 advisory 锁模拟
    在途 promote。
    """
    from server.app.db.transaction import write_transaction
    from server.app.services.job_staged_cleanup import delete_rerun_artifact_objects

    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    store = JobArtifactObjectStore(job_db, storage)
    up_key = f"jobs/{workspace['id']}/{job['id']}/up.json"

    # 升级/rerun 已在事务内删除清单行（模拟 post-commit 清理的输入态）；
    # 在途 promote 已把新字节 copy 到 authority key（行尚未登记）。
    from contextlib import closing

    from server.app.db.connection import connect_database

    with closing(connect_database(job_db.dsn_identity)) as conn, conn:
        conn.execute("delete from job_artifacts where job_id=%s", (job["id"],))
    storage.objects[up_key] = b"new generation bytes"
    storage.deleted.clear()
    stale_rows = [{"node_key": "up", "name": "up.json", "storage_key": up_key}]

    with write_transaction(job_db.dsn_identity) as promote_conn:
        # 在途 promote：持 artifact-authority 锁（copy 与登记之间的中段）。
        promote_conn.execute(
            "select pg_advisory_xact_lock(hashtext(%s))", (f"artifact-authority:{up_key}",)
        )
        delete_rerun_artifact_objects(store, stale_rows, job["id"], "rerun")
        # 锁被持有 → cleanup 跳过删除，新字节存活。
        assert up_key not in storage.deleted
        assert storage.objects[up_key] == b"new generation bytes"
        # promote 后段：登记清单行并提交（锁随提交释放）。
        store.record_remote(
            workspace_id=str(workspace["id"]),
            job_id=job["id"],
            node_key="up",
            name="up.json",
            storage_key=up_key,
            size_bytes=20,
            content_hash="hash-new",
        )

    # 锁释放后再清理：锁可得，但清单行已登记 → 复核命中，同样不删。
    delete_rerun_artifact_objects(store, stale_rows, job["id"], "rerun")
    assert up_key not in storage.deleted
    assert storage.objects[up_key] == b"new generation bytes"
