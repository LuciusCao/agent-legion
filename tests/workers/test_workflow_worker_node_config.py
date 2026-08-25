"""Dispatch-time code-pool node config injection (spec D15, P-0.5)."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.services.node_codes import NodeCodeService
from server.app.services.vault import VaultService
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from tests.helpers import scan_entries
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _local_node, _make_definition

SCHEMA = {
    "type": "object",
    "properties": {
        "bank_version": {"type": "string", "default": "v5"},
        "country_id": {"type": "string"},
        "token": {"type": "string", "secret": True},
    },
}


def _make_worker(
    tmp_path: Path,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
) -> WorkflowWorkerThread:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
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
            "workflows": {"enabled": True},
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
    worker.state.scan_entries = scan_entries(*definitions)
    return worker


def _prepare_job(
    job_db: JobQueries,
    node: WorkflowNode,
    *,
    workspace: dict | None = None,
    run_id: str = "",
) -> tuple[dict, dict]:
    ws = workspace or job_db.create_workspace("Test WS", default_workflow_key="test")
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id=run_id,
        title="Q1",
        node_keys=[node.key],
        workspace_id=ws["id"],
    )
    # Since #96 every code node needs published node code to dispatch; the
    # RecordingExecutor never reads it, so a trivial version is enough.
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        ws["id"], "test", node.key, "def run(job, job_dir, runtime):\n    pass\n", "test seed"
    )
    codes.publish(ws["id"], "test", node.key)
    return ws, job


def test_dispatch_injects_live_node_config_chain(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config={"bank_version": "v9"},
        config_schema=SCHEMA,
        outputs=["output.json"],
    )
    definition = _make_definition([node])
    ws, _job = _prepare_job(job_db, node)
    job_db.update_workspace(ws["id"], node_config={"test": {"fetch": {"country_id": "9"}}})
    worker = _make_worker(tmp_path, executor, [definition])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker.state.futures.values():
        future.result(timeout=5)

    # schema default ← workflow node config ← workspace override; the
    # platform-reserved execution keys merge in with platform defaults.
    assert executor.contexts[0].node_config == {
        "bank_version": "v9",
        "country_id": "9",
        "timeout_seconds": 600,
        "sandbox_network": False,
    }
    worker.stop()


def test_dispatch_prefers_frozen_batch_node_config(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = _local_node(
        "fetch",
    )
    ws = job_db.create_workspace("Test WS", default_workflow_key="test")
    batch = job_db.create_run(
        "test",
        "batch_by_ids",
        {"node_config": {"fetch": {"bank_version": "frozen"}}},
        ws["id"],
    )
    _ws, _job = _prepare_job(job_db, node, workspace=ws, run_id=str(batch["id"]))
    # The freeze lives on the job row (RUN-FREEZE-001), not the run payload.
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({"fetch": {"bank_version": "frozen"}}), _job["id"]),
        )
    job_db.update_workspace(ws["id"], node_config={"test": {"fetch": {"bank_version": "v9"}}})
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker.state.futures.values():
        future.result(timeout=5)

    # The intake snapshot wins over any live layer; reserved execution keys
    # absent from the old frozen payload are padded from the node's declared
    # config (platform defaults here), frozen values always win (P-0.5).
    assert executor.contexts[0].node_config == {
        "bank_version": "frozen",
        "timeout_seconds": 600,
        "sandbox_network": False,
    }
    worker.stop()


def test_dispatch_pads_frozen_batch_with_node_declared_reserved_values(tmp_path: Path) -> None:
    """v47-harvested nodes carry reserved values in config; a frozen batch
    predating the reserved keys is padded from them (Step 1 behavior: the
    executor capability seeded them — Step 2 moved the seed onto the node)."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config={"timeout_seconds": 900, "sandbox_network": True},
        outputs=["output.json"],
    )
    ws = job_db.create_workspace("Test WS", default_workflow_key="test")
    batch = job_db.create_run(
        "test",
        "batch_by_ids",
        {"node_config": {"fetch": {"bank_version": "frozen"}}},
        ws["id"],
    )
    _ws, _job = _prepare_job(job_db, node, workspace=ws, run_id=str(batch["id"]))
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({"fetch": {"bank_version": "frozen"}}), _job["id"]),
        )
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker.state.futures.values():
        future.result(timeout=5)

    assert executor.contexts[0].node_config == {
        "bank_version": "frozen",
        "timeout_seconds": 900,
        "sandbox_network": True,
    }
    worker.stop()


def test_dispatch_fails_node_on_invalid_workspace_override(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config_schema=SCHEMA,
        outputs=["output.json"],
    )
    _ws, job = _prepare_job(job_db, node)
    job_db.update_workspace(_ws["id"], node_config={"test": {"fetch": {"nope": 1}}})
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])

    worker._poll()

    failed = job_db.get_job_node(job["id"], "fetch")
    assert failed is not None
    assert failed["status"] == "failed"
    assert "unknown keys" in failed["error_message"]
    assert not executor.contexts
    worker.stop()


def test_dispatch_resolves_vault_secret_refs_in_memory(tmp_path: Path, monkeypatch) -> None:
    """Frozen/stored configs carry only secret_ref markers; the dispatch path
    resolves them to plaintext in memory before invoking the executor
    (VAULT-SECRET-001)."""
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config_schema=SCHEMA,
        outputs=["output.json"],
    )
    ws, _job = _prepare_job(job_db, node)
    name = "node:test:fetch:token"
    VaultService(TEST_DATABASE_URL, {}).set(ws["id"], name, "dispatch-plain-token")
    job_db.update_workspace(
        ws["id"],
        node_config={"test": {"fetch": {"bank_version": "v9", "token": {"secret_ref": name}}}},
    )
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker.state.futures.values():
        future.result(timeout=5)

    # The executor sees the resolved plaintext; the marker never leaves the server.
    assert executor.contexts[0].node_config == {
        "bank_version": "v9",
        "token": "dispatch-plain-token",
        "timeout_seconds": 600,
        "sandbox_network": False,
    }
    worker.stop()


def test_dispatch_fails_node_on_unresolvable_secret_ref(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("code")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config_schema=SCHEMA,
        outputs=["output.json"],
    )
    ws, job = _prepare_job(job_db, node)
    job_db.update_workspace(
        ws["id"],
        node_config={"test": {"fetch": {"token": {"secret_ref": "node:test:fetch:token"}}}},
    )
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])

    worker._poll()

    failed = job_db.get_job_node(job["id"], "fetch")
    assert failed is not None
    assert failed["status"] == "failed"
    assert "not found" in failed["error_message"]
    assert not executor.contexts
    worker.stop()
