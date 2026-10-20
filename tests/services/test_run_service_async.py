"""RunService async accept + batch control plane (#1117, runs-async mode).

The sync creation path is pinned by test_run_service.py; this file owns the
``create_run_async`` accept semantics (202 handle, deterministic-id parity,
blind-retry self-heal) and the pause/resume/cancel CAS contract. Feeder
consumption lives in test_run_batch_feeder.py; the HTTP surface in
tests/routes/test_runs_async_api.py.
"""

from __future__ import annotations

import pytest

from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WORKFLOW_KEY = "education_video_problems_generation"
WORKSPACE_ID = "ws-run-async"


def _workspace_with_revision(job_db, settings, workspace_id: str = WORKSPACE_ID) -> dict:
    workspace = job_db.create_workspace(workspace_id)
    seed_demo_workspace_node_codes(settings, workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], load_builtin_definition(WORKFLOW_KEY)
    )
    return workspace


def _insert_material(job_db, workspace_id: str, material_id: str, *, status: str = "ready") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, %s, 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
                status,
            ),
        )


def _material_item(material_id: str) -> dict:
    return {"type": "material", "material_id": material_id}


@pytest.fixture
def service(job_db, settings) -> RunService:
    _workspace_with_revision(job_db, settings)
    return RunService(job_db, settings)


def test_async_accept_returns_handle_without_creating_jobs(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")

    result = service.create_run_async(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )

    run, batch = result["run"], result["batch"]
    assert run["source_kind"] == "items" and run["status"] == "created"
    assert run["created_count"] == 0
    assert batch["run_id"] == run["id"]
    assert batch["status"] == "pending"
    assert batch["items_total"] == 1 and batch["processed_count"] == 0
    assert batch["jobs_created"] == 0 and batch["chunk_errors"] == []
    # 受理不建 job：解析/去重/插入全部推迟到 feeder。
    assert job_db.count_jobs_in_run(run["id"]) == 0


def _accept_text_items(job_db, workspace_id: str) -> None:
    """Republish the demo workflow with a text-accepting entry contract."""
    import copy

    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )


def test_async_accepts_text_item_only_when_storage_configured(service, job_db) -> None:
    _accept_text_items(job_db, WORKSPACE_ID)
    # materials_service=None → fail-closed 503 precondition, same as sync.
    with pytest.raises(MaterialStorageUnavailableError):
        service.create_run_async(
            WORKSPACE_ID,
            workflow_key=WORKSPACE_ID,
            items=[{"type": "text", "content": "需求"}],
        )


def test_async_accept_runs_the_shared_fail_fast_checks(service, job_db) -> None:
    # 未发布 revision 的已存在 workspace → 400 族（不存在的 workspace 是 404，
    # 由 get_workspace 先行拦住，与同步路径同序）。
    job_db.create_workspace("ws-no-revision")
    with pytest.raises(InvalidOperationError, match="no active workflow revision"):
        service.create_run_async(
            "ws-no-revision", workflow_key="ws-no-revision", items=[_material_item("m")]
        )
    with pytest.raises(InvalidOperationError, match="At least one item"):
        service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[])
    # 入口契约：demo workflow 的 start 节点只接受 material。
    with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
        service.create_run_async(
            WORKSPACE_ID,
            workflow_key=WORKSPACE_ID,
            items=[{"type": "ref", "connection_key": "c", "external_id": "e"}],
        )
    # 被拒的提交不留 run/batch 行（fail-fast 在任何写入之前）。
    assert service.list_runs(WORKSPACE_ID) == []


def test_async_blind_retry_is_idempotent(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]

    first = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
    second = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)

    assert second["run"]["id"] == first["run"]["id"]
    assert second["batch"]["status"] == "pending"
    assert second["batch"]["items_total"] == 1
    # 幂等：只有一行 run、一行 batch。
    assert len(service.list_runs(WORKSPACE_ID)) == 1


def test_async_run_id_matches_sync_digest(service, job_db) -> None:
    """同一 items 在两模式下解析到同一确定性 run id（digest 逐字节同形）。

    async 受理后 sync 提交同一 items：sync 臂 upsert 复用同一 run 行并把
    job 挂到它下面——盲重试跨模式不失效。
    """
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]

    accepted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
    created = service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)

    assert created["run"]["id"] == accepted["run"]["id"]
    assert created["created_count"] == 1


def test_resubmit_after_cancel_resets_the_batch(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]
    accepted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
    run_id = accepted["run"]["id"]

    cancelled = service.cancel_run_batch(WORKSPACE_ID, run_id)
    assert cancelled["batch"]["status"] == "cancelled"

    resubmitted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
    assert resubmitted["run"]["id"] == run_id
    assert resubmitted["batch"]["status"] == "pending"
    assert resubmitted["batch"]["processed_count"] == 0


def test_resubmit_heals_failed_run(service, job_db) -> None:
    """failed run 的同 digest 重提：run 行治愈（failed→created）+ batch 重置。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]
    run_id = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)["run"][
        "id"
    ]
    job_db.fail_run_batch(run_id, "corrupt spec")
    job_db.update_intake_run(run_id, status="failed", error_message="corrupt spec")

    resubmitted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)

    assert resubmitted["run"]["status"] == "created"
    assert resubmitted["run"]["error_message"] == ""
    assert resubmitted["batch"]["status"] == "pending"


def test_resubmit_completed_not_depleted_is_a_no_op(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]
    run_id = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)["run"][
        "id"
    ]
    feeder = RunBatchFeeder(job_db, settings=service.settings)
    while feeder.consume_once():
        pass
    assert job_db.get_run_batch(run_id)["status"] == "completed"

    resubmitted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
    assert resubmitted["batch"]["status"] == "completed"
    assert resubmitted["batch"]["jobs_created"] == 1


def test_resubmit_completed_but_depleted_requeues(service, job_db) -> None:
    """completed 后 job 被删（live < 记录值）→ 同 digest 重提重新排队重喂。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    _insert_material(job_db, WORKSPACE_ID, "mat-2")
    items = [_material_item("mat-1"), _material_item("mat-2")]
    run_id = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)["run"][
        "id"
    ]
    feeder = RunBatchFeeder(job_db, settings=service.settings)
    while feeder.consume_once():
        pass
    assert job_db.get_run_batch(run_id)["jobs_created"] == 2
    with job_db.connect() as conn:
        conn.execute("delete from jobs where run_id=%s and source_id=%s", (run_id, "mat-2"))

    resubmitted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)

    assert resubmitted["batch"]["status"] == "pending"
    # 重喂只补缺口：dedup 探测滤掉 mat-1 的在存 job。
    while feeder.consume_once():
        pass
    detail = service.get_run(WORKSPACE_ID, run_id)
    assert detail["batch"]["status"] == "completed"
    assert detail["batch"]["jobs_created"] == 2


def test_get_run_detail_carries_the_batch_handle(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = service.create_run_async(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )["run"]["id"]

    detail = service.get_run(WORKSPACE_ID, run_id)
    assert detail["batch"]["run_id"] == run_id
    assert detail["batch"]["status"] == "pending"
    assert detail["job_stats"] == {"total": 0, "by_status": {}}


def test_sync_run_detail_has_no_batch(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = service.create_run(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )["run"]["id"]
    assert service.get_run(WORKSPACE_ID, run_id)["batch"] is None


def test_pause_resume_cancel_transitions(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = service.create_run_async(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )["run"]["id"]

    paused = service.pause_run_batch(WORKSPACE_ID, run_id)
    assert paused["batch"]["status"] == "paused"
    # 暂停语义幂等失败（CAS miss → 409 族），不静默成功。
    with pytest.raises(ConflictError, match="not feeding"):
        service.pause_run_batch(WORKSPACE_ID, run_id)
    # 暂停的 batch 不被 feeder 认领。
    assert RunBatchFeeder(job_db, settings=service.settings).consume_once() is False

    resumed = service.resume_run_batch(WORKSPACE_ID, run_id)
    assert resumed["batch"]["status"] == "pending"
    with pytest.raises(ConflictError, match="not paused"):
        service.resume_run_batch(WORKSPACE_ID, run_id)

    cancelled = service.cancel_run_batch(WORKSPACE_ID, run_id)
    assert cancelled["batch"]["status"] == "cancelled"
    with pytest.raises(ConflictError, match="already terminal"):
        service.cancel_run_batch(WORKSPACE_ID, run_id)
    # 取消只停 feeder：已建 job 保留（此处为 0），run 行不翻转。
    assert service.get_run(WORKSPACE_ID, run_id)["run"]["status"] == "created"


def test_control_plane_404s(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    sync_run_id = service.create_run(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )["run"]["id"]
    # 同步 run 没有 batch 句柄。
    with pytest.raises(NotFoundError, match="Run batch not found"):
        service.pause_run_batch(WORKSPACE_ID, sync_run_id)
    # 跨 workspace 与不存在 id 同样 404（防枚举）。
    with pytest.raises(NotFoundError, match="Run not found"):
        service.cancel_run_batch(WORKSPACE_ID, "no-such-run")
