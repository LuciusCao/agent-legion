"""Claim-route secret injection failure: 500, no request loss, sweeper retry.

The claim transaction commits before secret injection runs on the response
path (``agent_worker_claims.py``): a vault failure (e.g. the referenced
``secret_ref`` was deleted) returns 500, the Worker drops the attempt, and
the sweeper requeues the request once the lease expires — a later claim
then succeeds with the restored reference.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app.agent_broker import AgentExecutionRequest
from server.app.services.vault import VaultService
from tests.routes.test_agent_workers import _make_app, _register

_CLAIM_URL = "/api/agent-executions/claim"

_SCHEMA = {
    "properties": {
        "mode": {"type": "string"},
        "token": {"type": "string", "secret": True},
    }
}


def _enqueue_code_request(app, job_db, *, job_id: str) -> str:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'question_comprehension_info')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'package')", (job_id,))
    execution_id = app.state.agent_broker.enqueue(
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


def _claim(client: TestClient, token: str):
    return client.post(
        _CLAIM_URL,
        headers={"X-Agent-Worker-Token": token},
        json={"worker_id": "home-mini"},
    )


def _request_state(job_db, execution_id: str) -> str:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"])


def test_code_claim_secret_failure_500_then_sweeper_requeue_retries(
    tmp_path: Path, monkeypatch, job_db
) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    app = _make_app(tmp_path)
    execution_id = _enqueue_code_request(app, job_db, job_id="job-1")
    vault = VaultService(job_db.path, {})
    vault.set("test-workspace", "api-token", "s3cr3t")
    # The referenced vault entry is gone by the time the claim is served.
    vault.delete("test-workspace", "api-token")

    with TestClient(app) as client:
        token = _register(
            client, capabilities=["package"], max_code_concurrency=1, protocol_version=2
        )["worker_token"]

        failed = _claim(client, token)
        assert failed.status_code == 500
        assert failed.json()["detail"] == "code manifest secret resolution failed"

        # The claim committed before injection failed: the request is not
        # lost, it sits claimed behind the expired-soon lease.
        assert _request_state(job_db, execution_id) == "claimed"
        # The claimed zombie holds the only code slot; no second claim is
        # handed out until the sweeper requeues it.
        assert _claim(client, token).status_code == 204

        with job_db.connect() as conn:
            conn.execute(
                "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
                (datetime.now(UTC) - timedelta(days=1), execution_id),
            )
        assert app.state.agent_broker.sweep_expired_claims() == [execution_id]
        assert _request_state(job_db, execution_id) == "queued"

        # Restore the reference: the retry succeeds with the live value.
        vault.set("test-workspace", "api-token", "restored-secret")
        claimed = _claim(client, token)

    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    assert body["execution_id"] == execution_id
    assert body["kind"] == "code"
    assert body["manifest"]["config"] == {"mode": "fast", "token": "restored-secret"}
    assert "secret_config" not in body["manifest"]
