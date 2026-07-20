"""Page-facing read-only ``GET /api/remote/workers`` (phase 4, task 9, Decision 10).

The frontend executors store polls this endpoint from the same-origin page
context, so unlike the worker-action endpoints it requires no worker token;
it still reports 503 while remote execution is disabled. The response
projects the registry including ``labels`` and the ``revoked`` flag, and
revoked workers stay listed (operators need to see them).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.routes.remote import create_remote_router

ADMIN_TOKEN = "admin-global-token"
ADMIN_HEADERS = {"X-Worker-Token": ADMIN_TOKEN}


def _remote_settings(settings, *, worker_token: str = ADMIN_TOKEN):
    remote = settings.executor_runtime.remote.model_copy(update={"worker_token": worker_token})
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    return dataclasses.replace(settings, executor_runtime=runtime)


@pytest.fixture
def rig(tmp_path: Path, settings):
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, _remote_settings(settings)), prefix="/api")
    return TestClient(app), broker


def _register(client: TestClient, worker_id: str, **overrides) -> None:
    body = {
        "worker_id": worker_id,
        "name": worker_id,
        "capabilities": ["cap_a"],
        "slots": 1,
        **overrides,
    }
    resp = client.post("/api/remote/workers/register", json=body, headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text


def test_workers_allows_page_access_without_token(rig) -> None:
    client, _ = rig
    _register(client, "w1", name="mac-mini")

    resp = client.get("/api/remote/workers")

    assert resp.status_code == 200
    workers = resp.json()["workers"]
    assert len(workers) == 1
    assert workers[0]["worker_id"] == "w1"
    assert workers[0]["name"] == "mac-mini"


def test_workers_response_includes_labels_and_revoked_fields(rig) -> None:
    client, _ = rig
    _register(client, "w1", labels={"device": "mac-mini", "mem_gb": 16})

    resp = client.get("/api/remote/workers")

    assert resp.status_code == 200
    worker = resp.json()["workers"][0]
    assert worker["capabilities"] == ["cap_a"]
    assert worker["slots"] == 1
    assert worker["labels"] == {"device": "mac-mini", "mem_gb": 16}
    assert worker["revoked"] is False
    assert worker["last_seen_at"]


def test_workers_lists_revoked_workers(rig) -> None:
    client, _ = rig
    _register(client, "w1")
    _register(client, "w2")
    revoke = client.post("/api/remote/workers/w1/revoke", headers=ADMIN_HEADERS)
    assert revoke.status_code == 204

    resp = client.get("/api/remote/workers")

    assert resp.status_code == 200
    workers = {w["worker_id"]: w for w in resp.json()["workers"]}
    assert workers["w1"]["revoked"] is True
    assert workers["w2"]["revoked"] is False


def test_workers_503_when_remote_disabled(tmp_path: Path, settings) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(
        create_remote_router(broker, _remote_settings(settings, worker_token="")), prefix="/api"
    )
    client = TestClient(app)

    resp = client.get("/api/remote/workers")

    assert resp.status_code == 503
