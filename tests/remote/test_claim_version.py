from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.routes.remote import create_remote_router
from tests.postgres_support import TEST_DATABASE_URL

ADMIN_TOKEN = "admin-token"


@contextmanager
def _client(tmp_path: Path, settings, min_version: int) -> Iterator[tuple[TestClient, str]]:
    remote = settings.executor_runtime.remote.model_copy(
        update={
            "worker_token": ADMIN_TOKEN,
            "min_worker_protocol_version": min_version,
        }
    )
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    configured = dataclasses.replace(settings, executor_runtime=runtime)
    db_path = TEST_DATABASE_URL
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, configured), prefix="/api")
    client = TestClient(app)
    issued = client.post(
        "/api/remote/workers/register",
        json={
            "worker_id": "w1",
            "name": "w1",
            "capabilities": ["cap_a"],
            "slots": 1,
        },
        headers={"X-Worker-Token": ADMIN_TOKEN},
    )
    assert issued.status_code == 201
    try:
        yield client, str(issued.json()["worker_token"])
    finally:
        broker.close()


def _claim(client: TestClient, token: str, **body: int) -> int:
    return client.post(
        "/api/remote/claim",
        json={"worker_id": "w1", "capabilities": ["cap_a"], **body},
        headers={"X-Worker-Token": token, "X-Worker-Id": "w1"},
    ).status_code


def test_claim_without_version_is_rejected(tmp_path: Path, settings) -> None:
    with _client(tmp_path, settings, 1) as (client, token):
        assert _claim(client, token) == 409


def test_claim_with_current_version_is_accepted(tmp_path: Path, settings) -> None:
    with _client(tmp_path, settings, 1) as (client, token):
        assert _claim(client, token, worker_version=1) == 204


def test_claim_below_min_version_is_rejected(tmp_path: Path, settings) -> None:
    with _client(tmp_path, settings, 2) as (client, token):
        assert _claim(client, token, worker_version=1) == 409


def test_min_version_zero_is_escape_hatch(tmp_path: Path, settings) -> None:
    with _client(tmp_path, settings, 0) as (client, token):
        assert _claim(client, token) == 204
