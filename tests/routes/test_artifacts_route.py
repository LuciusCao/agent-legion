from __future__ import annotations

import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.routes.artifacts import create_artifacts_router
from server.app.services.artifact_store import ArtifactStore
from tests.postgres_support import TEST_DATABASE_URL

TOKEN = "test-token"
HEADERS = {"X-Agent-Worker-Token": TOKEN}


class _WorkerAuthenticatorStub:
    def authenticate(self, token: str):  # noqa: ANN201
        if token == TOKEN:
            return {"worker_id": "w1"}
        return None


@pytest.fixture
def worker_settings(settings):
    workers = settings.executor_runtime.agent_workers.model_copy(update={"register_token": TOKEN})
    runtime = settings.executor_runtime.model_copy(update={"agent_workers": workers})
    return dataclasses.replace(settings, executor_runtime=runtime)


@pytest.fixture
def rig(tmp_path, worker_settings):
    init_db(TEST_DATABASE_URL)
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    app = FastAPI()
    app.include_router(
        create_artifacts_router(store, worker_settings, _WorkerAuthenticatorStub()),
        prefix="/api",
    )
    return TestClient(app), store


def test_post_and_get_round_trip(rig):
    client, _ = rig
    resp = client.post("/api/artifacts", headers=HEADERS, content=b"artifact-bytes")
    assert resp.status_code == 201
    h = resp.json()["hash"]
    assert len(h) == 64
    resp = client.get(f"/api/artifacts/{h}", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.content == b"artifact-bytes"


def test_post_unauthorized_without_token(rig):
    client, _ = rig
    resp = client.post("/api/artifacts", content=b"x")
    assert resp.status_code == 401


def test_get_unauthorized_without_token(rig):
    client, _ = rig
    resp = client.get(f"/api/artifacts/{'ab' * 32}")
    assert resp.status_code == 401


def test_get_malformed_hash_returns_404(rig):
    client, _ = rig
    resp = client.get(f"/api/artifacts/{'zz' * 32}", headers=HEADERS)
    assert resp.status_code == 404


def test_get_unknown_hash_returns_404(rig):
    client, _ = rig
    resp = client.get(f"/api/artifacts/{'ab' * 32}", headers=HEADERS)
    assert resp.status_code == 404


def test_post_413_when_too_large(tmp_path, settings):
    workers = settings.executor_runtime.agent_workers.model_copy(
        update={"register_token": TOKEN, "max_archive_bytes": 4}
    )
    runtime = settings.executor_runtime.model_copy(update={"agent_workers": workers})
    small_settings = dataclasses.replace(settings, executor_runtime=runtime)
    init_db(TEST_DATABASE_URL)
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    app = FastAPI()
    app.include_router(
        create_artifacts_router(store, small_settings, _WorkerAuthenticatorStub()),
        prefix="/api",
    )
    client = TestClient(app)
    resp = client.post("/api/artifacts", headers=HEADERS, content=b"more-than-four-bytes")
    assert resp.status_code == 413


def test_post_413_by_declared_content_length(tmp_path, settings):
    # An oversize declared Content-Length is rejected before the body is
    # buffered, even when the actual body would pass the post-read check.
    workers = settings.executor_runtime.agent_workers.model_copy(
        update={"register_token": TOKEN, "max_archive_bytes": 4}
    )
    runtime = settings.executor_runtime.model_copy(update={"agent_workers": workers})
    small_settings = dataclasses.replace(settings, executor_runtime=runtime)
    init_db(TEST_DATABASE_URL)
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    app = FastAPI()
    app.include_router(
        create_artifacts_router(store, small_settings, _WorkerAuthenticatorStub()),
        prefix="/api",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/artifacts",
        headers={**HEADERS, "Content-Length": "100"},
        content=b"xy",
    )
    assert resp.status_code == 413
