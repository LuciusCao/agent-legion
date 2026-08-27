"""Sweeper requeue closes the code claim loop: secrets re-injected live.

Batch 2 (design §7): the queued kind='code' manifest persists only vault
``secret_ref`` markers (VAULT-SECRET-001) and the claim-response path
resolves them on the fly. A lease-expired code claim is requeued by the
sweeper without touching ``manifest_json``, so the re-claim must inject the
CURRENT vault value — never empty, never a stale copy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.code_dispatch import resolve_code_manifest_config
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.vault import VaultService
from tests.postgres_support import TEST_DATABASE_URL

_SCHEMA = {
    "properties": {
        "mode": {"type": "string"},
        "token": {"type": "string", "secret": True},
    }
}


def _broker(data_dir) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir, lease_ttl_seconds=1)


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


def _enqueue_code(broker: AgentExecutionBroker, *, job_id: str) -> str:
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key="package",
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "workspace_id": "test-workspace",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "config_schema": _SCHEMA,
                "config": {"mode": "fast"},
                "secret_config": {"token": {"secret_ref": "api-token"}},
            },
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
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def test_sweeper_requeue_reclaim_reinjects_current_secret(job_db, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    vault = VaultService(TEST_DATABASE_URL, {})
    vault.set("test-workspace", "api-token", "first-secret")
    execution_id = _enqueue_code(broker, job_id="job-1")
    _register_code_worker()

    first = broker.claim("worker-code")
    assert first is not None
    resolved_first = resolve_code_manifest_config(first.manifest, TEST_DATABASE_URL, {})
    assert resolved_first["config"] == {"mode": "fast", "token": "first-secret"}

    # The lease expires without a heartbeat; the sweeper requeues the row.
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )
    assert broker.sweep_expired_claims() == [execution_id]

    # The requeue must not touch the persisted manifest: still references,
    # never plaintext.
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state, manifest_json from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row["state"] == "queued"
    manifest = json.loads(row["manifest_json"])
    assert manifest["secret_config"] == {"token": {"secret_ref": "api-token"}}
    assert "first-secret" not in row["manifest_json"]

    # Rotate the vault value while the request sits queued.
    vault.set("test-workspace", "api-token", "rotated-secret")

    second = broker.claim("worker-code")
    assert second is not None
    assert second.execution_id == execution_id
    assert second.lease_id != first.lease_id
    # The stored manifest still carries only the reference; injection happens
    # on the response path and resolves the CURRENT value.
    assert second.manifest["secret_config"] == {"token": {"secret_ref": "api-token"}}
    resolved_second = resolve_code_manifest_config(second.manifest, TEST_DATABASE_URL, {})
    assert resolved_second["config"] == {"mode": "fast", "token": "rotated-secret"}
    assert "secret_config" not in resolved_second
