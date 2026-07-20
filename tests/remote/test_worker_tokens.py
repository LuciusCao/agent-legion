"""Per-worker token issuance, authentication, revocation, legacy fallback (phase 4, task 4).

Covers the SEC-WORKER-001 trust model: the management (global static) token
issues per-worker tokens via ``POST /api/remote/workers/register``; only the
sha256 of the secret part is persisted; claim/heartbeat/result/bundle accept a
per-worker token or — during the fallback window — the legacy global token;
revocation takes effect immediately and beats the fallback window.
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
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteExecutionPayload
from server.app.routes.remote import create_remote_router

ADMIN_TOKEN = "admin-global-token"
ADMIN_HEADERS = {"X-Worker-Token": ADMIN_TOKEN}


def _fetchone(db_path: Path, sql: str, params: tuple = ()):
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql, params).fetchone()


def _remote_settings(settings, *, allow_legacy: bool = True):
    remote = settings.executor_runtime.remote.model_copy(
        update={"worker_token": ADMIN_TOKEN, "allow_legacy_worker_token": allow_legacy}
    )
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    return dataclasses.replace(settings, executor_runtime=runtime)


@pytest.fixture
def rig(tmp_path: Path, settings):
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, _remote_settings(settings)), prefix="/api")
    return TestClient(app), broker, db_path


def _register(client: TestClient, worker_id: str, **overrides):
    body = {
        "worker_id": worker_id,
        "name": worker_id,
        "capabilities": ["cap_a"],
        "slots": 1,
        **overrides,
    }
    return client.post("/api/remote/workers/register", json=body, headers=ADMIN_HEADERS)


def _register_ok(client: TestClient, worker_id: str, **overrides) -> str:
    resp = _register(client, worker_id, **overrides)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["worker_token"])


def _claim(client: TestClient, worker_id: str, token: str):
    return client.post(
        "/api/remote/claim",
        json={"worker_id": worker_id, "capabilities": ["cap_a"]},
        headers={"X-Worker-Token": token, "X-Worker-Id": worker_id},
    )


def _submit_task(broker: RemoteExecutionBroker, execution_id: str = "e1") -> None:
    broker.bundle_dir.mkdir(parents=True, exist_ok=True)
    (broker.bundle_dir / f"{execution_id}.tar.gz").write_bytes(b"bundle-bytes")
    broker.submit(
        RemoteExecutionPayload(
            execution_id=execution_id,
            lease_id=f"lease-{execution_id}",
            job_id="job1",
            node_key="node_a",
            capability="cap_a",
            bundle_name=f"{execution_id}.tar.gz",
            manifest={"job_id": "job1", "node_key": "node_a", "run_token": "abc123"},
        )
    )


# ---- matrix 1: register endpoint requires the management token ----


def test_register_requires_management_token(rig) -> None:
    client, _, _ = rig
    body = {"worker_id": "w1", "capabilities": ["cap_a"], "slots": 1}
    assert client.post("/api/remote/workers/register", json=body).status_code == 401
    wrong = client.post(
        "/api/remote/workers/register",
        json=body,
        headers={"X-Worker-Token": "not-the-admin-token"},
    )
    assert wrong.status_code == 401


# ---- matrix 2 + 11: successful register issues a token; only the hash persists ----


def test_register_issues_token_and_stores_only_hash(rig) -> None:
    client, _, db_path = rig
    resp = _register(client, "w1", labels={"device": "mac-mini", "mem_gb": 16})
    assert resp.status_code == 201
    token = str(resp.json()["worker_token"])
    worker_id, sep, secret = token.partition(".")
    assert worker_id == "w1" and sep == "." and secret

    row = _fetchone(
        db_path,
        "select token_hash, labels_json, revoked_at from remote_workers where worker_id = 'w1'",
    )
    assert row is not None
    token_hash, labels_json, revoked_at = row
    assert token_hash == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert token_hash != secret
    assert secret not in (labels_json or "")
    assert revoked_at is None
    # No plaintext anywhere in the row.
    full_row = _fetchone(db_path, "select * from remote_workers where worker_id = 'w1'")
    assert all(secret not in str(value) for value in full_row)
    assert '"device": "mac-mini"' in labels_json


# ---- matrix 3: correct per-worker token claims (empty queue -> 204, task -> 200) ----


def test_per_worker_token_claim_empty_queue(rig) -> None:
    client, _, _ = rig
    token = _register_ok(client, "w1")
    assert _claim(client, "w1", token).status_code == 204


def test_per_worker_token_claim_with_task(rig) -> None:
    client, broker, _ = rig
    token = _register_ok(client, "w1")
    _submit_task(broker)
    resp = _claim(client, "w1", token)
    assert resp.status_code == 200
    assert resp.json()["execution_id"] == "e1"


# ---- matrix 4: wrong secret -> 401 ----


def test_wrong_secret_per_worker_token_rejected(rig) -> None:
    client, _, _ = rig
    token = _register_ok(client, "w1")
    bad = f"{token.split('.', 1)[0]}.{'x' * 43}"
    assert _claim(client, "w1", bad).status_code == 401


# ---- matrix 5: cross-mixing worker A's id with worker B's secret -> 401 ----


def test_cross_mixed_worker_id_and_secret_rejected(rig) -> None:
    client, _, _ = rig
    _register_ok(client, "wA")
    token_b = _register_ok(client, "wB")
    mixed = f"wA.{token_b.split('.', 1)[1]}"
    assert _claim(client, "wA", mixed).status_code == 401


# ---- matrix 6: legacy global token passes during the fallback window ----


def test_legacy_token_allowed_during_fallback_window(rig) -> None:
    client, _, _ = rig
    assert _claim(client, "w-legacy", ADMIN_TOKEN).status_code == 204


# ---- matrix 7: legacy global token rejected once the window closes ----


def test_legacy_token_rejected_when_window_closed(tmp_path: Path, settings) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    app = FastAPI()
    app.include_router(
        create_remote_router(broker, _remote_settings(settings, allow_legacy=False)),
        prefix="/api",
    )
    client = TestClient(app)
    assert _claim(client, "w-legacy", ADMIN_TOKEN).status_code == 401
    # Per-worker tokens still work after the window closes.
    token = _register_ok(client, "w1")
    assert _claim(client, "w1", token).status_code == 204


# ---- matrix 8: revocation rejects the per-worker token immediately ----


def test_revoked_worker_token_rejected_immediately(rig) -> None:
    client, _, _ = rig
    token = _register_ok(client, "w1")
    assert _claim(client, "w1", token).status_code == 204
    revoke = client.post("/api/remote/workers/w1/revoke", headers=ADMIN_HEADERS)
    assert revoke.status_code == 204
    assert _claim(client, "w1", token).status_code == 401


# ---- matrix 9: revocation beats the fallback window ----


def test_revoked_worker_rejected_even_with_legacy_token(rig) -> None:
    client, _, _ = rig
    _register_ok(client, "w1")
    assert client.post("/api/remote/workers/w1/revoke", headers=ADMIN_HEADERS).status_code == 204
    assert _claim(client, "w1", ADMIN_TOKEN).status_code == 401


# ---- matrix 10: revoking an unknown worker -> 404 ----


def test_revoke_unknown_worker_returns_404(rig) -> None:
    client, _, _ = rig
    resp = client.post("/api/remote/workers/ghost/revoke", headers=ADMIN_HEADERS)
    assert resp.status_code == 404
    # Management token is still required for revoke.
    assert client.post("/api/remote/workers/ghost/revoke").status_code == 401


# ---- broker layer: idempotent re-issue invalidates the old token ----


def test_issue_worker_token_reissue_rotates_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")

    first = broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    assert broker.authenticate_worker(first) is not None

    second = broker.issue_worker_token("w1", "w1", ["cap_a"], 2, {"device": "pi"})
    assert second != first
    assert broker.authenticate_worker(first) is None  # old secret dies immediately
    record = broker.authenticate_worker(second)
    assert record is not None
    assert record["worker_id"] == "w1"
    assert record["slots"] == 2
    assert record["labels"] == {"device": "pi"}

    row = _fetchone(db_path, "select token_hash from remote_workers where worker_id = 'w1'")
    assert row[0] == hashlib.sha256(second.split(".", 1)[1].encode("utf-8")).hexdigest()


# ---- broker layer: malformed tokens authenticate as None ----


@pytest.mark.parametrize(
    "token",
    [
        "",
        "no-dot-separator",
        ".secret-only",
        "worker-only.",
        "w1." + "x" * 43,  # well-formed but unknown secret
    ],
)
def test_authenticate_worker_malformed_tokens_return_none(tmp_path: Path, token: str) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    assert broker.authenticate_worker(token) is None


# ---- broker layer: labels must be flat scalars ----


@pytest.mark.parametrize("bad_value", [{"nested": 1}, ["list"], None, (1, 2)])
def test_issue_worker_token_rejects_non_scalar_labels(tmp_path: Path, bad_value) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    with pytest.raises(ValueError, match="labels"):
        broker.issue_worker_token("w1", "w1", ["cap_a"], 1, {"bad": bad_value})


def test_issue_worker_token_accepts_scalar_labels(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    labels = {"device": "mac-mini", "mem_gb": 16, "weight": 1.5, "gpu": False}
    token = broker.issue_worker_token("w1", "w1", ["cap_a"], 1, labels)
    record = broker.authenticate_worker(token)
    assert record is not None
    assert record["labels"] == labels


# ---- broker layer: revoke_worker rowcount semantics ----


def test_revoke_worker_rowcount_semantics(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    assert broker.revoke_worker("ghost") is False
    broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    assert broker.revoke_worker("w1") is True
    assert broker.is_worker_revoked("w1") is True
    assert broker.is_worker_revoked("ghost") is False


def test_reissue_after_revoke_reonboards_worker(tmp_path: Path) -> None:
    """Re-issuing via the management endpoint is the operator re-onboarding
    path: it rotates the hash and clears the revocation."""
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    assert broker.revoke_worker("w1") is True
    fresh = broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    assert broker.is_worker_revoked("w1") is False
    assert broker.authenticate_worker(fresh) is not None


# ---- worker.py: startup exchange of a register token for a per-worker token ----


def test_worker_client_exchanges_register_token() -> None:
    import json

    from scripts.remote.worker import WorkerClient

    client = WorkerClient("http://server", "management-token", "w1")
    seen: dict[str, object] = {}

    def fake_request(method, path, *, body=None, headers=None):
        seen["method"], seen["path"] = method, path
        seen["body"] = body
        return 201, b'{"worker_token": "w1.per-worker-secret"}'

    client._request = fake_request  # type: ignore[method-assign]
    token = client.register_worker("Mac mini", ["cap_a"], 4)
    assert token == "w1.per-worker-secret"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/remote/workers/register"
    assert json.loads(seen["body"]) == {
        "worker_id": "w1",
        "name": "Mac mini",
        "capabilities": ["cap_a"],
        "slots": 4,
    }


def test_worker_client_register_token_failure_raises() -> None:
    from scripts.remote.worker import WorkerClient

    client = WorkerClient("http://server", "bad-token", "w1")
    client._request = lambda *a, **k: (401, b"unauthorized")  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="register"):
        client.register_worker("w1", ["cap_a"], 1)
