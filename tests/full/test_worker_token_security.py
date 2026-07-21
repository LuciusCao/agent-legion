"""Full-gate evidence for SECURITY-WORKER-001.

End-to-end worker trust scenario over the real HTTP stack and a real SQLite
registry: a per-worker token is issued by the management endpoint, authenticates
claims, and revocation takes effect on the very next request. The static
management token is never accepted by worker-facing endpoints. The registry row proves only the
sha256 of the secret persists.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.routes.remote import create_remote_router

pytestmark = pytest.mark.full_gate

ADMIN_TOKEN = "full-gate-admin-token"


def _claim(client: TestClient, worker_id: str, token: str) -> int:
    return client.post(
        "/api/remote/claim",
        json={"worker_id": worker_id, "capabilities": ["cap_a"], "worker_version": 1},
        headers={"X-Worker-Token": token, "X-Worker-Id": worker_id},
    ).status_code


def test_revocation_is_immediate_and_beats_fallback_window(tmp_path: Path, settings) -> None:
    remote = settings.executor_runtime.remote.model_copy(update={"worker_token": ADMIN_TOKEN})
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    remote_settings = dataclasses.replace(settings, executor_runtime=runtime)
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, remote_settings), prefix="/api")
    client = TestClient(app)

    issued = client.post(
        "/api/remote/workers/register",
        json={"worker_id": "w1", "name": "w1", "capabilities": ["cap_a"], "slots": 1},
        headers={"X-Worker-Token": ADMIN_TOKEN},
    )
    assert issued.status_code == 201
    token = str(issued.json()["worker_token"])
    secret = token.split(".", 1)[1]

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "select token_hash, revoked_at from remote_workers where worker_id = 'w1'"
        ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert row[1] is None

    assert _claim(client, "w1", token) == 204

    revoked = client.post("/api/remote/workers/w1/revoke", headers={"X-Worker-Token": ADMIN_TOKEN})
    assert revoked.status_code == 204

    # Immediate effect: the per-worker token dies on the next request, and the
    # static management token is always rejected on worker-facing endpoints.
    assert _claim(client, "w1", token) == 401
    assert _claim(client, "w1", ADMIN_TOKEN) == 401

    # Re-issuing through the management endpoint re-onboards the worker.
    reissued = client.post(
        "/api/remote/workers/register",
        json={"worker_id": "w1", "name": "w1", "capabilities": ["cap_a"], "slots": 1},
        headers={"X-Worker-Token": ADMIN_TOKEN},
    )
    assert reissued.status_code == 201
    fresh_token = str(reissued.json()["worker_token"])
    assert fresh_token != token
    assert _claim(client, "w1", fresh_token) == 204
