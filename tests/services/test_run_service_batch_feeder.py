"""RunBatchFeeder consumption tests (#1117): the drip-feed loop's semantics.

Drives ``consume_once`` synchronously (the asyncio wrapper is
run_batch_background, tested here only for its transient-failure survival,
mirroring test_job_intake_background.py). Covers: full feed, chunk error
containment, watermark gate, pause-wins CAS, stale claim reclaim, and the
runs.status discipline (never queued/processing — claim_intake_run compat).
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.run_batch_background import consume_run_batches
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WORKFLOW_KEY = "education_video_problems_generation"
WORKSPACE_ID = "ws-run-feeder"


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
def workspace(job_db, settings) -> str:
    _workspace_with_revision(job_db, settings)
    return WORKSPACE_ID


def _accept(job_db, settings, items: list[dict]) -> str:
    return RunService(job_db, settings).create_run_async(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items
    )["run"]["id"]


def _drain(feeder: RunBatchFeeder) -> None:
    while feeder.consume_once():
        pass


def test_feeder_creates_jobs_and_completes(workspace, job_db, settings) -> None:
    for index in range(3):
        _insert_material(job_db, WORKSPACE_ID, f"mat-{index}")
    run_id = _accept(job_db, settings, [_material_item(f"mat-{i}") for i in range(3)])

    _drain(RunBatchFeeder(job_db, settings))

    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "completed"
    assert batch["next_index"] == 3 and batch["jobs_created"] == 3
    assert batch["finished_at"] is not None
    run = job_db.get_run(run_id)
    # runs.status 纪律：completed，绝不取 queued/processing（intake 认领面）。
    assert run["status"] == "completed"
    assert int(run["created_count"]) == 3
    with job_db.connect() as conn:
        rows = conn.execute(
            "select source_id from jobs where run_id=%s order by source_id", (run_id,)
        ).fetchall()
    assert [row["source_id"] for row in rows] == ["mat-0", "mat-1", "mat-2"]


def test_feeder_chunk_error_skips_only_the_failed_chunk(
    workspace, job_db, settings, monkeypatch
) -> None:
    """chunk 粒度容错（intake 先例）：坏条目所在 chunk 整段跳过并记录，其余
    chunk 照常建 job，batch 仍推进到 completed。"""
    monkeypatch.setattr("server.app.services.run_batch_feeder.RUN_BATCH_CHUNK_SIZE", 1)
    _insert_material(job_db, WORKSPACE_ID, "mat-ok")
    run_id = _accept(job_db, settings, [_material_item("mat-missing"), _material_item("mat-ok")])

    _drain(RunBatchFeeder(job_db, settings))

    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "completed"
    assert batch["jobs_created"] == 1
    errors = json.loads(batch["chunk_errors_json"])
    assert len(errors) == 1 and errors[0]["chunk_start"] == 0
    assert "mat-missing" in errors[0]["error"]
    run = job_db.get_run(run_id)
    assert "mat-missing" in run["error_message"]


def test_feeder_dedup_filters_preexisting_jobs(workspace, job_db, settings) -> None:
    """异步模式把全量去重从 400 推迟为完成态事实：同 digest 下 sync 臂已建
    job，async 重提的 feeder 全程 dedup 滤过，零新增。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    sync_run_id = RunService(job_db, settings).create_run(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[_material_item("mat-1")]
    )["run"]["id"]
    run_id = _accept(job_db, settings, [_material_item("mat-1")])
    assert run_id == sync_run_id  # digest 跨模式一致

    _drain(RunBatchFeeder(job_db, settings))

    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "completed"
    # jobs_created 按 run 全量重算（count_jobs_in_run）——含 sync 臂建的那个；
    # feeder 自身零新增（dedup 探测滤掉）。
    assert batch["jobs_created"] == 1
    assert job_db.count_jobs_in_run(run_id) == 1


def test_watermark_gate_releases_the_claim(workspace, job_db, settings, monkeypatch) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = _accept(job_db, settings, [_material_item("mat-1")])
    feeder = RunBatchFeeder(job_db, settings)
    # 水位归零 → 任何 backlog 都视为超限（空 backlog 0 >= 0 同样挡）。
    monkeypatch.setattr(feeder, "_watermark", lambda: 0)

    assert feeder.consume_once() is False  # no-work：loop 进入慢档
    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "pending"  # 放回待认领，不卡 running
    assert job_db.count_jobs_in_run(run_id) == 0


def test_pause_wins_over_the_feeder_advance(workspace, job_db, settings) -> None:
    """advance 的 CAS 只在 running 上生效：chunk 中途 pause 落地则游标不推进，
    resume 后整 chunk 经 dedup 幂等重放。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = _accept(job_db, settings, [_material_item("mat-1")])
    feeder = RunBatchFeeder(job_db, settings)

    claimed = job_db.claim_run_batch()
    assert claimed is not None
    job_db.transition_run_batch(run_id, from_statuses=("running",), to_status="paused")
    feeder._consume_chunk(claimed)

    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "paused" and batch["next_index"] == 0
    # chunk 的 job 已提交（resume 重放由 dedup 吸收）。
    assert job_db.count_jobs_in_run(run_id) == 1

    job_db.transition_run_batch(run_id, from_statuses=("paused",), to_status="pending")
    _drain(feeder)
    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "completed" and batch["jobs_created"] == 1
    assert job_db.count_jobs_in_run(run_id) == 1  # 重放没有双建


def test_stale_running_claim_is_reclaimed(workspace, job_db, settings) -> None:
    """崩溃回收：running 停滞超阈值的 batch 被重新认领并喂完。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    run_id = _accept(job_db, settings, [_material_item("mat-1")])
    with job_db.connect() as conn:
        conn.execute(
            "update run_batches set status='running',"
            " updated_at=current_timestamp - interval '1 hour' where run_id=%s",
            (run_id,),
        )

    _drain(RunBatchFeeder(job_db, settings))

    assert job_db.get_run_batch(run_id)["status"] == "completed"
    assert job_db.count_jobs_in_run(run_id) == 1


def test_batch_level_failure_marks_failed_and_heals_on_resubmit(
    workspace, job_db, settings
) -> None:
    """簿记级失败（target_spec 损坏）→ batch failed + run failed；同 digest
    重提自愈（受理臂的 guarded reset）。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    items = [_material_item("mat-1")]
    run_id = _accept(job_db, settings, items)
    with job_db.connect() as conn:
        conn.execute("update run_batches set target_spec_json='{' where run_id=%s", (run_id,))

    feeder = RunBatchFeeder(job_db, settings)
    assert feeder.consume_once() is True

    assert job_db.get_run_batch(run_id)["status"] == "failed"
    assert job_db.get_run(run_id)["status"] == "failed"

    resubmitted = RunService(job_db, settings).create_run_async(
        WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items
    )
    assert resubmitted["batch"]["status"] == "pending"
    _drain(RunBatchFeeder(job_db, settings))
    assert job_db.get_run_batch(run_id)["status"] == "completed"
    assert job_db.get_run(run_id)["status"] == "completed"


def test_feeder_materializes_text_items(workspace, job_db, settings) -> None:
    """text 条目族：feeder 按 chunk 物化为材料（content-hash 幂等）再解析，
    与同步路径产出的 job input 逐字节同形。"""
    import copy

    from server.app.services.materials import MaterialsService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict
    from tests.fakes.storage import FakeObjectStorage

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        WORKSPACE_ID, workflow_definition_from_dict(raw)
    )
    materials = MaterialsService(job_db.dsn_identity, FakeObjectStorage())
    run_id = RunService(job_db, settings, materials_service=materials).create_run_async(
        WORKSPACE_ID,
        workflow_key=WORKSPACE_ID,
        items=[{"type": "text", "content": "证明：三角形内角和"}],
    )["run"]["id"]

    _drain(RunBatchFeeder(job_db, settings, materials_service=materials))

    batch = job_db.get_run_batch(run_id)
    assert batch["status"] == "completed" and batch["jobs_created"] == 1
    with job_db.connect() as conn:
        row = conn.execute("select * from jobs where run_id=%s", (run_id,)).fetchone()
        material = conn.execute(
            "select status, filename from materials where workspace_id=%s", (WORKSPACE_ID,)
        ).fetchone()
    assert json.loads(row["input_json"])["type"] == "material"
    assert material["status"] == "ready"


def test_consume_run_batches_survives_transient_failure() -> None:
    """消费循环生命维持：consume_once 抛错不杀死循环（intake 同款回归）。"""

    class FlakyFeeder:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self.calls = 0
            self.second_call = asyncio.Event()
            self._loop = loop

        def consume_once(self) -> bool:
            self.calls += 1
            if self.calls >= 2:
                self._loop.call_soon_threadsafe(self.second_call.set)
            if self.calls == 1:
                raise RuntimeError("transient db failure")
            return False

    async def _run() -> int:
        feeder = FlakyFeeder(asyncio.get_running_loop())
        task = asyncio.create_task(consume_run_batches(feeder, failure_backoff_seconds=0.01))
        try:
            await asyncio.wait_for(feeder.second_call.wait(), timeout=5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return feeder.calls

    calls = asyncio.run(_run())
    assert calls >= 2
