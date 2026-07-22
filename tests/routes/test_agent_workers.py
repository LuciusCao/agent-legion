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
    # Workspace dispatch defaults to paused (reset at every startup); the
    # operator resume is part of the environment these tests exercise.
    app.state.workspace_worker_control.resume("test-workspace")
    return app


def _register(client: TestClient, credential: str = "management-secret", **overrides) -> dict:
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
    response = client.post(
        "/api/agent-workers/register",
        headers={"X-Agent-Worker-Register-Token": credential},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


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
        token = _register(client)["worker_token"]
        assert app.state.job_db.get_job_node("job-1", "generate")["status"] == "pending"
        claimed = _claim(client, token)

    assert claimed["agent_id"] == "generator-v1"
    assert claimed["lease_id"]
    assert app.state.job_db.get_job_node("job-1", "generate")["status"] == "running"


def test_heartbeat_requires_and_validates_lease_id(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
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
        token = _register(client)["worker_token"]
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
        upgraded = _register(client, protocol_version=2)["worker_token"]
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
        token = _register(client)["worker_token"]
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
        token = _register(client)["worker_token"]
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


def test_agent_register_token_management_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        # Management endpoints require the global management token.
        unauthenticated = client.post("/api/agent-register-tokens", json={})
        assert unauthenticated.status_code == 401
        wrong = client.post(
            "/api/agent-register-tokens",
            headers={"X-Agent-Worker-Register-Token": "nope"},
            json={},
        )
        assert wrong.status_code == 401

        created = client.post(
            "/api/agent-register-tokens",
            headers=_MANAGEMENT,
            json={"workspace_id": "test-workspace", "label": "home mini"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["workspace_id"] == "test-workspace"
        assert body["label"] == "home mini"
        plaintext = body["register_token"]
        assert plaintext.startswith(f"{body['token_id']}.")

        # The list view never carries secret material.
        listed = client.get("/api/agent-register-tokens", headers=_MANAGEMENT)
        assert listed.status_code == 200
        entry = next(t for t in listed.json()["tokens"] if t["token_id"] == body["token_id"])
        assert entry["workspace_id"] == "test-workspace"
        assert entry["revoked"] is False
        assert "token_hash" not in entry
        assert "register_token" not in entry

        # A scoped token for a nonexistent workspace is rejected.
        missing = client.post(
            "/api/agent-register-tokens",
            headers=_MANAGEMENT,
            json={"workspace_id": "no-such-workspace"},
        )
        assert missing.status_code == 400

        # Revoke kills the credential.
        revoked = client.post(
            f"/api/agent-register-tokens/{body['token_id']}/revoke", headers=_MANAGEMENT
        )
        assert revoked.status_code == 200
        assert revoked.json() == {"revoked": True}
        register_after_revoke = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": plaintext},
            json={"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1},
        )
        assert register_after_revoke.status_code == 401
        unknown = client.post("/api/agent-register-tokens/no-such/revoke", headers=_MANAGEMENT)
        assert unknown.status_code == 404


def test_register_with_scoped_token_stores_and_returns_scope(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        created = client.post(
            "/api/agent-register-tokens",
            headers=_MANAGEMENT,
            json={"workspace_id": "test-workspace", "label": "scoped"},
        )
        scoped = created.json()["register_token"]

        scoped_registration = _register(client, credential=scoped, worker_id="scoped-worker")
        assert scoped_registration["allowed_workspaces"] == ["test-workspace"]

        global_registration = _register(client, worker_id="global-worker")
        assert global_registration["allowed_workspaces"] == []

        listed = client.get("/api/agent-workers")
        assert listed.status_code == 200
        workers = {w["worker_id"]: w for w in listed.json()["workers"]}
        assert workers["scoped-worker"]["allowed_workspaces"] == ["test-workspace"]
        assert workers["global-worker"]["allowed_workspaces"] == []

        # Re-registering with the global credential refreshes the scope.
        refreshed = _register(client, worker_id="scoped-worker")
        assert refreshed["allowed_workspaces"] == []


def test_worker_online_flag_tracks_last_seen(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["online"] is True

        # Age the Worker beyond the online threshold: registered but offline.
        with app.state.job_db.connect() as conn:
            conn.execute(
                "update agent_workers set last_seen_at = current_timestamp - interval '1 hour'"
            )
        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["online"] is False

        # Any authenticated Worker call (here: an empty claim poll) refreshes
        # last_seen_at and flips the Worker back online.
        response = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "home-mini"},
        )
        assert response.status_code == 204
        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["online"] is True


def _archive_with_events(events_lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        payload = ("\n".join(events_lines) + "\n").encode()
        info = tarfile.TarInfo("runs/generate/worker/events.jsonl")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_result_run_dir_promotes_events_for_logs_and_token_usage(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token, "X-Agent-Lease-Id": claimed["lease_id"]}

        unsafe = client.post(
            f"/api/agent-executions/{execution_id}/result",
            headers={
                **auth,
                "X-Agent-Result": json.dumps({"status": "completed", "run_dir": "../escape"}),
            },
            content=_empty_archive(),
        )
        assert unsafe.status_code == 400

        # The scheduler normally creates the job dir; the seeded job went
        # straight to the broker, so create it here.
        (app.state.settings.jobs_dir / "job-1").mkdir(parents=True, exist_ok=True)
        events = [
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "provider": "gateway",
                        "model": "test-model",
                        "usage": {"input": 120, "output": 34, "cacheRead": 5},
                    },
                }
            )
        ]
        ok = client.post(
            f"/api/agent-executions/{execution_id}/result",
            headers={
                **auth,
                "X-Agent-Result": json.dumps(
                    {"status": "completed", "run_dir": "runs/generate/worker"}
                ),
            },
            content=_archive_with_events(events),
        )
        assert ok.status_code == 204, ok.text

    run_dir = app.state.settings.data_dir / "jobs" / "job-1" / "runs" / "generate" / "worker"
    assert (run_dir / "events.jsonl").is_file() or (run_dir / "events.jsonl.gz").is_file()
    with app.state.job_db._connect_read() as conn:
        node_run = conn.execute("select id, run_dir from node_runs where job_id='job-1'").fetchone()
        usage = conn.execute(
            "select input_tokens, output_tokens from node_run_token_usage where node_run_id=?",
            (node_run["id"],),
        ).fetchone()
    assert node_run["run_dir"] == "jobs/job-1/runs/generate/worker"
    assert usage is not None
    assert (usage["input_tokens"], usage["output_tokens"]) == (120, 34)
