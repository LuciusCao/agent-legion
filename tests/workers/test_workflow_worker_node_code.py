"""Dispatch-time node code resolution (EXEC-CODE-002, post-#96).

Priority: job-frozen version → workspace published version → global factory
seed. With the gate disabled nothing resolves and the node fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.services.node_code_pins import node_code_pins_from_job_snapshot
from server.app.services.node_codes import NodeCodeService, code_hash
from server.app.services.workflow_revision_format import (
    definition_hash,
    serialize_definition,
)
from server.app.settings import Settings
from server.app.workflow_worker.code_claim import try_claim_code_worker_node
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _local_node, _make_definition

CUSTOM_V1 = "def run(job, job_dir, runtime):\n    return 'v1'\n"
CUSTOM_V2 = "def run(job, job_dir, runtime):\n    return 'v2'\n"


def _make_worker(
    tmp_path: Path,
    job_db: JobQueries,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
    *,
    custom_nodes_enabled: bool = True,
) -> WorkflowWorkerThread:
    leases = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        executor=executor,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        database_url=TEST_DATABASE_URL,
    )
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {
            "workflows": {"enabled": True, "custom_nodes_enabled": custom_nodes_enabled},
            "openclaw": {"command_template": ["openclaw"]},
            "code_capacity": 2,
        }
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        runtime=runtime,
        settings=settings,
    )
    worker._scan_entries = (definitions, [])
    return worker


def _prepare_job(tmp_path: Path, node: WorkflowNode, batch_payload: dict | None = None):
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="test")
    batch_id = ""
    if batch_payload is not None:
        batch_id = str(job_db.create_batch("test", "batch_by_ids", batch_payload, ws["id"])["id"])
    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id=batch_id,
        title="Q1",
        node_keys=[node.key],
        workspace_id=ws["id"],
    )
    return job_db, ws


def _dispatch(tmp_path: Path, worker: WorkflowWorkerThread) -> None:
    worker._poll()
    for future in worker._futures.values():
        future.result(timeout=5)


def test_dispatch_uses_published_custom_code(tmp_path: Path) -> None:
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_dispatch_prefers_frozen_code_version(tmp_path: Path) -> None:
    node = _local_node("fetch")
    codes_ws = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    seed_ws = codes_ws.create_workspace("Seed WS", default_workflow_key="test")
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(seed_ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(seed_ws["id"], "test", "fetch")
    frozen = {"fetch": {"version": 1, "code_hash": code_hash(CUSTOM_V1)}}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": frozen})
    # The frozen pin rides this job's batch; the workspace pins its own codes.
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V2, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    # The job froze v1 at intake; publishing v2 must not affect it.
    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_dispatch_fails_closed_when_gate_disabled(tmp_path: Path) -> None:
    """Gate off: no code resolves (not even a published one) and the node
    fails as a config error — there is no builtin fallback since #96."""
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(
        tmp_path, job_db, executor, [_make_definition([node])], custom_nodes_enabled=False
    )
    executor.block_event.set()

    worker._poll()

    assert executor.contexts == []
    job = job_db.list_jobs(workspace_id=ws["id"])[0]
    node_row = job_db.get_job_node(job["id"], "fetch")
    assert node_row["status"] == "failed"
    assert "no published node code" in node_row["error_message"]
    worker.stop()


def test_dispatch_capability_without_any_published_code_fails(tmp_path: Path) -> None:
    """No workspace version and no global seed: a clear config error."""
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    worker._poll()

    assert executor.contexts == []
    job = job_db.list_jobs(workspace_id=ws["id"])[0]
    node_row = job_db.get_job_node(job["id"], "fetch")
    assert node_row["status"] == "failed"
    assert "no published node code" in node_row["error_message"]
    worker.stop()


def test_dispatch_uses_global_factory_seed_when_workspace_has_none(tmp_path: Path) -> None:
    """The global factory-seeded version (demo nodes) serves workspaces
    without their own published code (#96)."""
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    assert codes.seed_global("test", "fetch", CUSTOM_V1, "test seed")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_workspace_published_shadows_global_seed(tmp_path: Path) -> None:
    """A workspace's own published version wins over the global factory seed."""
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    assert codes.seed_global("test", "fetch", CUSTOM_V1, "test seed")
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V2, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V2
    worker.stop()


def test_frozen_pin_resolves_global_seed_version(tmp_path: Path) -> None:
    """Intake freeze pins the global seed version; dispatch re-reads it
    scoped (workspace miss → global hit) and verifies the hash."""
    node = _local_node("fetch")
    codes = NodeCodeService(TEST_DATABASE_URL)
    assert codes.seed_global("test", "fetch", CUSTOM_V1, "test seed")
    frozen = {"fetch": {"version": 1, "code_hash": code_hash(CUSTOM_V1)}}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": frozen})
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def _pin(code: str, version: int) -> dict:
    return {"version": version, "code_hash": code_hash(code)}


def _attach_snapshot(job_db: JobQueries, ws: dict, node: WorkflowNode, pins: dict | None) -> dict:
    """Give the job a workflow snapshot like an upgraded revision's stored
    definition_json: pure definition plus (optionally) node_code_pins."""
    pure = serialize_definition(_make_definition([node]))
    payload = json.loads(pure)
    if pins is not None:
        payload["node_code_pins"] = pins
    snapshot = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    job = job_db.list_jobs(workspace_id=ws["id"])[0]
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json=%s,"
            " workflow_definition_hash=%s where id=%s",
            (snapshot, definition_hash(pure), job["id"]),
        )
    return job_db.get_job(job["id"])


def _publish_v1_v2(ws: dict) -> None:
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V2, "user:u1")
    codes.publish(ws["id"], "test", "fetch")


def test_dispatch_prefers_snapshot_pin_over_stale_batch_pin(tmp_path: Path) -> None:
    """#109: upgrade_workflow refreshed the job snapshot's node_code_pins to
    v2 while the intake batch still pins v1; dispatch must run v2."""
    node = _local_node("fetch")
    batch_pins = {"fetch": _pin(CUSTOM_V1, 1)}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": batch_pins})
    _publish_v1_v2(ws)
    _attach_snapshot(job_db, ws, node, {"fetch": _pin(CUSTOM_V2, 2)})
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V2
    worker.stop()


def test_dispatch_falls_back_to_batch_pin_when_snapshot_has_no_pins(tmp_path: Path) -> None:
    """Legacy compat: a snapshot without node_code_pins falls back to the
    intake batch's node_code_versions."""
    node = _local_node("fetch")
    batch_pins = {"fetch": _pin(CUSTOM_V1, 1)}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": batch_pins})
    _publish_v1_v2(ws)
    _attach_snapshot(job_db, ws, node, None)
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_code_worker_claim_prefers_snapshot_pin_over_stale_batch_pin(tmp_path: Path) -> None:
    """#109, code-Worker claim path: same pin priority as local dispatch."""
    node = _local_node("fetch")
    batch_pins = {"fetch": _pin(CUSTOM_V1, 1)}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": batch_pins})
    _publish_v1_v2(ws)
    fat = _attach_snapshot(job_db, ws, node, {"fetch": _pin(CUSTOM_V2, 2)})
    # Mirror what evaluate_job_ready puts on the ready candidate's lean job.
    job = {
        **fat,
        "workflow_definition_snapshot_json": "",
        "node_code_pins": node_code_pins_from_job_snapshot(fat),
    }
    dispatch = MagicMock()
    dispatch.is_in_flight.return_value = False
    dispatch.broker.has_active_request.return_value = False
    dispatch.online_code_worker_available.return_value = True
    dispatch.try_mark_in_flight.return_value = True
    dispatch.enqueue_pool.submit.side_effect = lambda fn: (fn(), True)[1]
    worker = MagicMock()
    worker.job_db = job_db
    worker.code_dispatch = dispatch
    worker._batch_payload_cache = {}
    worker._pass_claim_counts = {}
    worker.settings.root_dir = tmp_path
    worker.settings.config = {}
    worker.settings.executor_runtime.workflows.custom_nodes_enabled = True

    handled = try_claim_code_worker_node(
        worker,
        job_db.get_workspace(ws["id"]),
        job,
        node,
        tmp_path,
        tmp_path / "claim.log",
        (),
        "test",
    )

    assert handled is True
    assert dispatch.enqueue.call_args.kwargs["code_text"] == CUSTOM_V2
