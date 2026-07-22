from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.test_agent_broker import _seed_request

_MANAGEMENT = {"X-Agent-Worker-Register-Token": "management-secret"}


def _make_app(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.agent_workers.register_token = "management-secret"
    return app


def _register(client: TestClient, **overrides) -> str:
    payload = {
        "worker_id": "home-mini",
        "name": "Home Mac mini",
        "runtimes": ["pi"],
        "max_concurrency": 10,
        "labels": {"arch": "arm64"},
        "protocol_version": 1,
        "image_version": "agent-legion-worker:test",
    }
    payload.update(overrides)
    response = client.post("/api/agent-workers/register", headers=_MANAGEMENT, json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["worker_token"])


def _claim(client: TestClient, token: str) -> dict:
    response = client.post(
        "/api/agent-executions/claim",
        headers={"X-Agent-Worker-Token": token},
        json={"worker_id": "home-mini"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _empty_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz"):
        pass
    return buffer.getvalue()


def test_agent_worker_register_and_claim_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)
        assert app.state.job_db.get_job_node("job-1", "generate")["status"] == "pending"
        claimed = _claim(client, token)

    assert claimed["agent_id"] == "generator-v1"
    assert claimed["lease_id"]
    assert app.state.job_db.get_job_node("job-1", "generate")["status"] == "running"


def test_heartbeat_requires_and_validates_lease_id(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)
        claimed = _claim(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token}

        missing = client.post(f"/api/agent-executions/{execution_id}/heartbeat", headers=auth)
        assert missing.status_code == 400

        wrong = client.post(
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={**auth, "X-Agent-Lease-Id": "not-the-lease"},
        )
        assert wrong.status_code == 409

        ok = client.post(
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={**auth, "X-Agent-Lease-Id": claimed["lease_id"]},
        )
        assert ok.status_code == 204


def test_protocol_floor_is_enforced_after_registration(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)
        # Server raises its minimum after the worker registered at v1.
        app.state.settings.executor_runtime.agent_workers.min_protocol_version = 2
        stale = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "home-mini"},
        )
        assert stale.status_code == 409
        assert "protocol version" in stale.json()["detail"]
        # Re-registering at the new protocol restores access.
        upgraded = _register(client, protocol_version=2)
        assert _claim(client, upgraded)["lease_id"]


def test_register_rejects_malformed_worker_id_and_label_overflow(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        for bad_id in ("has.dot", "has space", "", "x" * 65):
            response = client.post(
                "/api/agent-workers/register",
                headers=_MANAGEMENT,
                json={
                    "worker_id": bad_id,
                    "runtimes": ["pi"],
                    "max_concurrency": 1,
                    "protocol_version": 1,
                },
            )
            assert response.status_code in (400, 422), (bad_id, response.status_code)

        too_many_labels = {f"key-{index}": "v" for index in range(33)}
        response = client.post(
            "/api/agent-workers/register",
            headers=_MANAGEMENT,
            json={
                "worker_id": "home-mini",
                "runtimes": ["pi"],
                "max_concurrency": 1,
                "labels": too_many_labels,
                "protocol_version": 1,
            },
        )
        assert response.status_code == 400


def test_result_rejects_bad_metadata_without_orphaning_archive(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)
        claimed = _claim(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token}

        no_lease = client.post(
            f"/api/agent-executions/{execution_id}/result",
            headers={**auth, "X-Agent-Result": json.dumps({"status": "failed"})},
            content=_empty_archive(),
        )
        assert no_lease.status_code == 400

        for bad_metadata in (
            "not json",
            json.dumps(["completed"]),
            json.dumps({"status": "failed", "exit_code": "abc"}),
            json.dumps({"status": "completed", "output_artifacts": ["x"]}),
            json.dumps({"status": "completed", "output_artifacts": {"a": "md5:deadbeef"}}),
        ):
            response = client.post(
                f"/api/agent-executions/{execution_id}/result",
                headers={
                    **auth,
                    "X-Agent-Lease-Id": claimed["lease_id"],
                    "X-Agent-Result": bad_metadata,
                },
                content=_empty_archive(),
            )
            assert response.status_code == 400, bad_metadata

        # Bad metadata must not leave an archive on disk nor retire the bundle.
        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert list(bundle_dir.glob("*.result.tar.gz")) == []

        # The claim is still alive for a valid retry.
        ok = client.post(
            f"/api/agent-executions/{execution_id}/heartbeat",
            headers={**auth, "X-Agent-Lease-Id": claimed["lease_id"]},
        )
        assert ok.status_code == 204


def test_result_rejects_oversized_archive(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    app.state.settings.executor_runtime.agent_workers.max_archive_bytes = 64
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps({"status": "failed", "exit_code": 1}),
            },
            content=b"x" * 1024,
        )
        assert response.status_code == 413
        # The declared-length gate fires before the body is written anywhere.
        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert not bundle_dir.exists() or list(bundle_dir.glob("*.result.tar.gz")) == []
