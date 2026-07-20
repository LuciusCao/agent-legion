from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
)
from server.app.routes.remote import create_remote_router
from server.app.settings import Settings

TOKEN = "test-token"
HEADERS = {"X-Worker-Token": TOKEN, "X-Worker-Id": "w1"}


@pytest.fixture
def remote_settings(settings):
    remote = settings.executor_runtime.remote.model_copy(update={"worker_token": TOKEN})
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    return dataclasses.replace(settings, executor_runtime=runtime)


@pytest.fixture
def rig(tmp_path, remote_settings):
    init_db(tmp_path / "jobs.sqlite")
    broker = RemoteExecutionBroker(tmp_path / "jobs.sqlite", tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, remote_settings), prefix="/api")
    return TestClient(app), broker


def _submit(
    broker: RemoteExecutionBroker,
    execution_id: str = "e1",
    command_spec: dict | None = None,
) -> None:
    broker.bundle_dir.mkdir(parents=True, exist_ok=True)
    (broker.bundle_dir / f"{execution_id}.tar.gz").write_bytes(b"bundle-bytes")
    broker.submit(
        RemoteExecutionPayload(
            execution_id=execution_id,
            lease_id="lease-e1",
            job_id="job1",
            node_key="node_a",
            capability="cap_a",
            bundle_name=f"{execution_id}.tar.gz",
            manifest={"job_id": "job1", "node_key": "node_a", "run_token": "abc123"},
            command_spec=command_spec,
        )
    )


def _settings_with_remote(settings: Settings, **updates: object) -> Settings:
    remote = settings.executor_runtime.remote.model_copy(update=updates)
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    return dataclasses.replace(settings, executor_runtime=runtime)


def _client_for(settings: Settings, tmp_path: Path) -> tuple[TestClient, RemoteExecutionBroker]:
    init_db(tmp_path / "jobs.sqlite")
    broker = RemoteExecutionBroker(tmp_path / "jobs.sqlite", tmp_path / "bundles")
    app = FastAPI()
    app.include_router(create_remote_router(broker, settings), prefix="/api")
    return TestClient(app), broker


def _claim(client: TestClient) -> None:
    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 200


def test_unauthorized_without_token(rig):
    client, _ = rig
    resp = client.post("/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]})
    assert resp.status_code == 401


def test_register_and_list_workers(rig):
    client, broker = rig
    resp = client.post(
        "/api/remote/register",
        json={"worker_id": "w1", "name": "mac-mini", "capabilities": ["cap_a"], "slots": 65},
        headers=HEADERS,
    )
    assert resp.status_code == 204
    assert broker.list_workers()[0]["worker_id"] == "w1"


def test_claim_empty_returns_204(rig):
    client, _ = rig
    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 204


def test_full_claim_report_cycle(rig):
    client, broker = rig
    _submit(broker)

    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 200
    claim = resp.json()
    assert claim["execution_id"] == "e1"
    assert claim["bundle_url"] == "/api/remote/executions/e1/bundle"
    assert claim["manifest"]["run_token"] == "abc123"

    resp = client.get("/api/remote/executions/e1/bundle", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.content == b"bundle-bytes"

    resp = client.post("/api/remote/executions/e1/heartbeat", headers=HEADERS)
    assert resp.status_code == 204

    meta = {
        "status": "completed",
        "exit_code": 0,
        "error_message": "",
        "command": ["pi"],
        "skill_version": "v",
    }
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"result-tar-bytes",
    )
    assert resp.status_code == 204

    outcome = broker.wait_result("e1")
    assert outcome.status == "completed"
    assert (broker.bundle_dir / "e1.result.tar.gz").read_bytes() == b"result-tar-bytes"


def test_claim_response_includes_command_spec(rig):
    client, broker = rig
    spec = {
        "version": 1,
        "prompt": "work in {job_dir}",
        "command": ["pi", "@{prompt_file}", "{session_name}"],
        "prompt_instruction": "Execute the attached node instructions.",
    }
    _submit(broker, command_spec=spec)

    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["command_spec"] == spec


def test_claim_response_command_spec_defaults_null(rig):
    # Upgrade window: a legacy submission without a spec serializes as null,
    # which old workers simply ignore.
    client, broker = rig
    _submit(broker)
    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["command_spec"] is None


def test_result_rejected_for_wrong_worker(rig):
    client, broker = rig
    _submit(broker)
    client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    meta = {"status": "completed", "exit_code": 0}
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={
            "X-Worker-Token": TOKEN,
            "X-Worker-Id": "w2",
            "X-Remote-Result": json.dumps(meta),
        },
        content=b"x",
    )
    assert resp.status_code == 409


def test_result_rejects_invalid_status(rig):
    client, broker = rig
    _submit(broker)
    client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps({"status": "bogus", "exit_code": 0})},
        content=b"x",
    )
    assert resp.status_code == 400


def test_heartbeat_unknown_execution(rig):
    client, _ = rig
    resp = client.post("/api/remote/executions/nope/heartbeat", headers=HEADERS)
    assert resp.status_code == 409


def test_bundle_404_for_unclaimed(rig):
    client, broker = rig
    _submit(broker)
    resp = client.get("/api/remote/executions/e1/bundle", headers=HEADERS)
    assert resp.status_code == 404


def test_service_unavailable_without_configured_token(tmp_path, settings):
    client, _ = _client_for(_settings_with_remote(settings, worker_token=""), tmp_path)
    resp = client.post(
        "/api/remote/claim",
        json={"worker_id": "w1", "capabilities": ["cap_a"]},
        headers=HEADERS,
    )
    assert resp.status_code == 503


def test_missing_worker_id_returns_400(rig):
    client, _ = rig
    resp = client.post(
        "/api/remote/claim",
        json={"worker_id": "w1", "capabilities": ["cap_a"]},
        headers={"X-Worker-Token": TOKEN},
    )
    assert resp.status_code == 400


def test_bundle_410_when_bundle_file_gone(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    (broker.bundle_dir / "e1.tar.gz").unlink()
    resp = client.get("/api/remote/executions/e1/bundle", headers=HEADERS)
    assert resp.status_code == 410


def test_result_413_when_archive_too_large(tmp_path, settings):
    client, broker = _client_for(
        _settings_with_remote(settings, worker_token=TOKEN, max_archive_bytes=4), tmp_path
    )
    _submit(broker)
    _claim(client)
    meta = {"status": "completed", "exit_code": 0}
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"more-than-four-bytes",
    )
    assert resp.status_code == 413


def test_result_missing_metadata_returns_400(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    resp = client.post("/api/remote/executions/e1/result", headers=HEADERS, content=b"x")
    assert resp.status_code == 400


def test_result_invalid_metadata_json_returns_400(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": "not-json"},
        content=b"x",
    )
    assert resp.status_code == 400


def test_duplicate_result_report_returns_409(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    meta = {"status": "completed", "exit_code": 0}
    first = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"x",
    )
    assert first.status_code == 204
    second = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"x",
    )
    assert second.status_code == 409


def test_duplicate_result_report_preserves_first_archive(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    meta = {"status": "completed", "exit_code": 0}
    first = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"first-archive-bytes",
    )
    assert first.status_code == 204
    second = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"clobbering-bytes",
    )
    assert second.status_code == 409
    assert (broker.bundle_dir / "e1.result.tar.gz").read_bytes() == b"first-archive-bytes"
    assert not (broker.bundle_dir / "e1.result.tar.gz.uploading").exists()


def test_result_non_dict_metadata_returns_400(rig):
    client, broker = rig
    _submit(broker)
    _claim(client)
    resp = client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": "[1]"},
        content=b"x",
    )
    assert resp.status_code == 400


def test_result_rejects_path_traversal_execution_id(rig, tmp_path):
    client, broker = rig
    _submit(broker)
    _claim(client)
    meta = {"status": "completed", "exit_code": 0}
    resp = client.post(
        "/api/remote/executions/..%2E%2Eevil/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"x",
    )
    assert resp.status_code == 400
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and broker.bundle_dir not in p.parents]
    assert [p.name for p in outside] == ["jobs.sqlite"]


def test_claim_worker_id_mismatch_returns_400(rig):
    client, _ = rig
    resp = client.post(
        "/api/remote/claim",
        json={"worker_id": "w2", "capabilities": ["cap_a"]},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "worker id mismatch"


def test_register_worker_id_mismatch_returns_400(rig):
    client, _ = rig
    resp = client.post(
        "/api/remote/register",
        json={"worker_id": "w2", "name": "x", "capabilities": ["cap_a"], "slots": 1},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "worker id mismatch"


def test_workers_allows_page_access_without_token(rig):
    # Decision 10 (phase 4): the executors store polls this endpoint from the
    # same-origin page context, so it no longer requires a worker token.
    client, _ = rig
    resp = client.get("/api/remote/workers")
    assert resp.status_code == 200


def test_workers_lists_registered(rig):
    client, _ = rig
    client.post(
        "/api/remote/register",
        json={"worker_id": "w1", "name": "mac-mini", "capabilities": ["cap_a"], "slots": 65},
        headers=HEADERS,
    )
    resp = client.get("/api/remote/workers", headers={"X-Worker-Token": TOKEN})
    assert resp.status_code == 200
    workers = resp.json()["workers"]
    assert len(workers) == 1
    assert workers[0]["worker_id"] == "w1"
    assert workers[0]["name"] == "mac-mini"
    assert workers[0]["capabilities"] == ["cap_a"]
    assert workers[0]["slots"] == 65
    assert workers[0]["registered_at"]
    assert workers[0]["last_seen_at"]


def test_workers_503_when_disabled(tmp_path, settings):
    client, _ = _client_for(settings, tmp_path)
    resp = client.get("/api/remote/workers", headers={"X-Worker-Token": "x"})
    assert resp.status_code == 503


def _wait_and_read_archive(
    broker: RemoteExecutionBroker,
    execution_id: str,
    archives: list[bytes],
    errors: list[BaseException],
) -> None:
    try:
        outcome = broker.wait_result(execution_id)
        # Executor-equivalent: open the archive the moment the outcome is
        # visible; a missing file here means the race regressed.
        archives.append((broker.bundle_dir / outcome.result_archive_name).read_bytes())
    except BaseException as exc:  # surfaced via assertion in the test body
        errors.append(exc)


def test_report_publishes_archive_before_outcome(rig):
    """A waiter must find the archive durable at its final name the instant
    wait_result returns — the outcome is never published before the rename."""
    client, broker = rig
    meta = {"status": "completed", "exit_code": 0}
    for i in range(25):
        execution_id = f"e{i}"
        _submit(broker, execution_id)
        _claim(client)
        content = f"archive-bytes-{i}".encode()
        errors: list[BaseException] = []
        archives: list[bytes] = []
        waiter = threading.Thread(
            target=_wait_and_read_archive, args=(broker, execution_id, archives, errors)
        )
        waiter.start()
        resp = client.post(
            f"/api/remote/executions/{execution_id}/result",
            headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
            content=content,
        )
        assert resp.status_code == 204
        waiter.join(timeout=5)
        assert not waiter.is_alive()
        assert errors == []
        assert archives == [content]
