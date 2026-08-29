"""Terminal-state manifest slimming for kind='code' rows (issue #142).

The queued manifest persists only a lightweight runtime_context audit stub;
rows enqueued before the fix still carry the full ~1.7MB context (the intake
``job_batch`` payload). Every code row reaching a terminal state
(``mark_done`` / ``cancel_request`` / requeue-limit-exceeded) is slimmed
back to the stub so ``agent_execution_requests`` never retains the heavy
payload after the execution is over.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim import cancel_request
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_LEGACY_RUNTIME = {
    "job": {"id": "job-1", "batch_id": "batch-1"},
    "workspace": {"id": "test-workspace"},
    "settings_config": {},
    "job_batch": {"id": "batch-1", "source_payload_json": '{"marker_142": "BIG"}'},
    "skill_versions": {"other": "v2"},
}

_STUB = {
    "job_id": "job-1",
    "workspace_id": "test-workspace",
    "batch_id": "batch-1",
    "batch_hash": None,
}


def _broker(data_dir, **kwargs) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir, **kwargs)


def _insert_code_job_rows(job_db, *, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'package')", (job_id,))


def _enqueue_code(
    broker: AgentExecutionBroker, *, job_id: str, runtime_context: dict | None = None
) -> str:
    manifest = {
        "kind": "code",
        "workspace_id": "test-workspace",
        "capability": "package",
        "code_hash": "abc123",
        "job_id": job_id,
        "log_path": f"logs/{job_id}.log",
    }
    if runtime_context is not None:
        manifest["runtime_context"] = runtime_context
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key="package",
            agent_id="package",
            agent_definition_hash="codehash",
            manifest=manifest,
            kind="code",
        )
    )
    assert execution_id is not None
    return execution_id


def _register_code_worker() -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-code",
        name="worker",
        runtimes=["pi"],
        capabilities=["package"],
        max_concurrency=10,
        max_code_concurrency=1,
        protocol_version=2,
    )


def _stored_manifest(job_db, execution_id: str) -> dict:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select manifest_json from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    return json.loads(row["manifest_json"])


def _assert_slimmed(manifest: dict) -> None:
    assert manifest["runtime_context"] == _STUB
    # The heavy payload is gone — the whole point of issue #142.
    assert "job_batch" not in json.dumps(manifest)


def test_mark_done_slims_legacy_code_manifest(job_db, tmp_path) -> None:
    _insert_code_job_rows(job_db, job_id="job-1")
    broker = _broker(tmp_path, lease_ttl_seconds=1)
    execution_id = _enqueue_code(broker, job_id="job-1", runtime_context=_LEGACY_RUNTIME)
    _register_code_worker()

    claimed = broker.claim("worker-code")
    assert claimed is not None
    # The claim response still carries the full context (the persisted legacy
    # copy) until the terminal transition slims it.
    assert claimed.manifest["runtime_context"]["job_batch"]["id"] == "batch-1"

    broker.mark_done(claimed.execution_id, "worker-code", claimed.lease_id, {"status": "completed"})

    manifest = _stored_manifest(job_db, execution_id)
    _assert_slimmed(manifest)


def test_cancel_request_slims_legacy_code_manifest(job_db, tmp_path) -> None:
    _insert_code_job_rows(job_db, job_id="job-1")
    broker = _broker(tmp_path)
    execution_id = _enqueue_code(broker, job_id="job-1", runtime_context=_LEGACY_RUNTIME)

    with write_transaction(TEST_DATABASE_URL) as conn:
        cancel_request(conn, execution_id)

    manifest = _stored_manifest(job_db, execution_id)
    _assert_slimmed(manifest)


def test_sweep_requeue_limit_exceeded_slims_legacy_code_manifest(job_db, tmp_path) -> None:
    _insert_code_job_rows(job_db, job_id="job-1")
    broker = _broker(tmp_path, lease_ttl_seconds=1, requeue_limit=0)
    execution_id = _enqueue_code(broker, job_id="job-1", runtime_context=_LEGACY_RUNTIME)
    _register_code_worker()
    claimed = broker.claim("worker-code")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )

    assert broker.sweep_expired_claims() == []

    manifest = _stored_manifest(job_db, execution_id)
    _assert_slimmed(manifest)


def test_terminal_transition_keeps_slim_stub_code_manifest(job_db, tmp_path) -> None:
    """Post-fix stub rows pass through the trim untouched (idempotent)."""
    _insert_code_job_rows(job_db, job_id="job-1")
    broker = _broker(tmp_path, lease_ttl_seconds=1)
    execution_id = _enqueue_code(broker, job_id="job-1", runtime_context=_STUB)
    _register_code_worker()

    claimed = broker.claim("worker-code")
    assert claimed is not None
    broker.mark_done(claimed.execution_id, "worker-code", claimed.lease_id, {"status": "completed"})

    manifest = _stored_manifest(job_db, execution_id)
    assert manifest["runtime_context"] == _STUB
    assert "job_batch" not in json.dumps(manifest)
