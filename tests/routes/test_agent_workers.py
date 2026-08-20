from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app.agent_broker import AgentExecutionRequest
from server.app.db.transaction import write_transaction
from server.app.main import create_app
from server.app.services.vault import VaultService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.test_agent_broker import _seed_request

_MANAGEMENT = {"X-Agent-Worker-Register-Token": "management-secret"}
_CSRF = {"x-agent-legion-request": "1"}


def _authenticate_admin(client: TestClient) -> None:
    """Bootstrap the first admin and keep its session cookie on the client."""
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "admin", "password": "admin-pw"},
    )
    assert response.status_code == 200, response.text
    client.headers["x-agent-legion-request"] = "1"


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
        "capabilities": ["generate"],
        "models": [{"provider": "gateway", "model": "test-model"}],
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
    assert response.json()["host_protocol_version"] == 3
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


def test_agent_worker_register_accepts_velites_runtime(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2, runtime="velites")

    with TestClient(app) as client:
        token = _register(client, runtimes=["pi", "velites"])["worker_token"]
        worker = client.get("/api/agent-workers/self", headers={"X-Agent-Worker-Token": token})
        claimed = _claim(client, token)

    assert worker.status_code == 200
    assert worker.json()["runtimes"] == ["pi", "velites"]
    assert claimed["agent_id"] == "generator-v1"
    assert app.state.job_db.get_job_node("job-1", "generate")["status"] == "running"


def test_agent_worker_register_rejects_unknown_runtime(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": "management-secret"},
            json={
                "worker_id": "home-mini",
                "name": "Home Mac mini",
                "runtimes": ["rust"],
                "max_concurrency": 1,
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "runtimes must contain pi, openclaw and/or velites"


def test_worker_can_read_only_its_own_status_with_issued_token(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        own_status = client.get(
            "/api/agent-workers/self",
            headers={"X-Agent-Worker-Token": token},
        )
        anonymous = client.get("/api/agent-workers/self")

    assert own_status.status_code == 200
    assert own_status.json()["worker_id"] == "home-mini"
    assert own_status.json()["name"] == "Home Mac mini"
    assert own_status.json()["revoked"] is False
    assert anonymous.status_code == 401


def test_worker_metrics_require_token_and_are_forced_to_own_worker(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(app.state.job_db.path) as conn:
        for worker_id, total_tokens in (("home-mini", 16), ("other-worker", 999)):
            conn.execute(
                """
                insert into ops_metric_samples(
                  bucket_start, worker_id, online_workers, active_executions,
                  input_tokens, output_tokens, cache_read_tokens, total_tokens
                ) values (%s, %s, 1, 0, 10, 5, 1, %s)
                """,
                (bucket, worker_id, total_tokens),
            )

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        path = "/api/agent-workers/self/metrics?granularity=6h&worker_id=other-worker"
        own = client.get(path, headers={"X-Agent-Worker-Token": token})
        anonymous = client.get(path)
        invalid = client.get(path, headers={"X-Agent-Worker-Token": "bad-token"})
        _authenticate_admin(client)
        session_only = client.get(path)

    assert own.status_code == 200
    rows = [row for row in own.json()["buckets"] if row["bucket_start"] == bucket.isoformat()]
    assert [row["total_tokens"] for row in rows] == [16]
    assert anonymous.status_code == 401
    assert invalid.status_code == 401
    assert session_only.status_code == 401


def test_claim_requires_matching_worker_capability_and_model(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        wrong_capability = _register(
            client, capabilities=["review"], models=[{"provider": "gateway", "model": "test-model"}]
        )["worker_token"]
        response = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": wrong_capability},
            json={"worker_id": "home-mini"},
        )
        assert response.status_code == 204

        wrong_model = _register(
            client, capabilities=["generate"], models=[{"provider": "gateway", "model": "other"}]
        )["worker_token"]
        response = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": wrong_model},
            json={"worker_id": "home-mini"},
        )
        assert response.status_code == 204


def test_queued_claim_uses_latest_execution_config_from_same_revision(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)
    definition = workflow_definition_from_mapping(
        {
            "key": "questions",
            "label": "Questions",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "execution": {"provider": "gateway", "model": "latest-model"},
                }
            },
        }
    )
    revision = WorkflowRevisionService(app.state.job_db).publish_workspace_revision(
        "test-workspace", definition
    )
    with app.state.job_db.connect() as conn:
        conn.execute("update jobs set workflow_revision_id=%s where id='job-1'", (revision["id"],))

    with TestClient(app) as client:
        token = _register(
            client,
            capabilities=["generate"],
            models=[{"provider": "gateway", "model": "latest-model"}],
        )["worker_token"]
        claimed = _claim(client, token)

    assert claimed["manifest"]["execution"]["model"] == "latest-model"


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


def test_release_slot_requires_and_validates_lease_id(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token}
        url = f"/api/agent-executions/{execution_id}/release-slot"

        missing = client.post(url, headers=auth)
        assert missing.status_code == 400

        wrong = client.post(url, headers={**auth, "X-Agent-Lease-Id": "not-the-lease"})
        assert wrong.status_code == 409

        ok = client.post(url, headers={**auth, "X-Agent-Lease-Id": claimed["lease_id"]})
        assert ok.status_code == 204

        # Released executions still accept the result report (reporting state).
        report = client.post(
            f"/api/agent-executions/{execution_id}/result",
            headers={
                **auth,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps({"status": "completed", "exit_code": 0}),
            },
            content=_empty_archive(),
        )
        assert report.status_code == 204

        # And the slot is gone afterwards: a second release is a conflict.
        again = client.post(url, headers={**auth, "X-Agent-Lease-Id": claimed["lease_id"]})
        assert again.status_code == 409


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
        # Management endpoints require an admin session (SECURITY-AUTH-001):
        # anonymous calls and the legacy register-token header both get 401.
        unauthenticated = client.post(
            "/api/agent-register-tokens",
            json={"workspace_id": "test-workspace", "label": "no header"},
        )
        assert unauthenticated.status_code == 401
        legacy = client.post(
            "/api/agent-register-tokens",
            headers={"X-Agent-Worker-Register-Token": "nope"},
            json={"label": "legacy header"},
        )
        assert legacy.status_code == 401
        _authenticate_admin(client)

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


def test_agent_worker_revoke_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        # Management endpoints require an admin session (SECURITY-AUTH-001):
        # anonymous calls and the legacy register-token header both get 401.
        unauthenticated = client.post("/api/agent-workers/no-such/revoke")
        assert unauthenticated.status_code == 401
        legacy = client.post(
            "/api/agent-workers/no-such/revoke",
            headers={"X-Agent-Worker-Register-Token": "nope"},
        )
        assert legacy.status_code == 401
        _authenticate_admin(client)

        token = _register(client)["worker_token"]
        revoked = client.post("/api/agent-workers/home-mini/revoke", headers=_MANAGEMENT)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json() == {"worker_id": "home-mini", "revoked": True}

        # The revoked Worker's credential is dead: authenticate rejects it, so
        # the claim poll fails before ever reaching the broker.
        claim = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "home-mini"},
        )
        assert claim.status_code in (401, 409)

        listed = client.get("/api/agent-workers").json()["workers"]
        assert listed[0]["revoked"] is True

        # Re-revoke is idempotent: the row still exists, so it succeeds again.
        again = client.post("/api/agent-workers/home-mini/revoke", headers=_MANAGEMENT)
        assert again.status_code == 200
        assert again.json() == {"worker_id": "home-mini", "revoked": True}


def test_register_with_scoped_token_stores_and_returns_scope(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
            "select input_tokens, output_tokens from node_run_token_usage where node_run_id=%s",
            (node_run["id"],),
        ).fetchone()
    assert node_run["run_dir"] == "jobs/job-1/runs/generate/worker"
    assert usage is not None
    assert (usage["input_tokens"], usage["output_tokens"]) == (120, 34)


# ---------------------------------------------------------------------------
# Batch 2: kind='code' protocol surface (dual pools, secret injection,
# heartbeat cancel body, auth-failure commit hook, node.log promotion).
# ---------------------------------------------------------------------------

_CODE = "def run(job, job_dir, runtime):\n    pass\n"


def _seed_code_request(
    app,
    *,
    job_id: str = "job-code-1",
    with_secret: bool = False,
    expected_outputs: list[str] | None = None,
) -> None:
    """Enqueue a self-contained kind='code' request straight into the broker."""
    with write_transaction(app.state.job_db.path) as conn:
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
    manifest = {
        "kind": "code",
        "capability": "package",
        "code_hash": "abc123",
        "job_id": job_id,
        "workspace_id": "test-workspace",
        "log_path": f"logs/jobs/{job_id}-package.log",
        "expected_outputs": list(expected_outputs or []),
        "config_schema": {
            "properties": {
                "mode": {"type": "string"},
                "token": {"type": "string", "secret": True},
            }
        },
        "config": {"mode": "fast"},
        "secret_config": {"token": {"secret_ref": "api-token"}} if with_secret else {},
        "bundle_name": f"{job_id}.code.tar.gz",
    }
    bundle_dir = Path(app.state.agent_broker.bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        payload = _CODE.encode()
        info = tarfile.TarInfo("node_code.py")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    (bundle_dir / str(manifest["bundle_name"])).write_bytes(buffer.getvalue())
    execution_id = app.state.agent_broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key="package",
            agent_id="package",
            agent_definition_hash="abc123",
            manifest=manifest,
            kind="code",
        )
    )
    assert execution_id is not None


def _register_code_worker(client: TestClient, **overrides) -> str:
    payload = {
        "worker_id": "code-worker",
        "runtimes": ["pi", "velites"],
        "capabilities": ["package"],
        "max_concurrency": 4,
        "max_code_concurrency": 2,
        "protocol_version": 2,
    }
    payload.update(overrides)
    response = client.post(
        "/api/agent-workers/register",
        headers=_MANAGEMENT,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["worker_token"])


def _claim_code(client: TestClient, token: str) -> dict:
    response = client.post(
        "/api/agent-executions/claim",
        headers={"X-Agent-Worker-Token": token},
        json={"worker_id": "code-worker", "max_code_concurrency": 2},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_register_roundtrips_code_capacity(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _register_code_worker(client)
        _register(client)  # legacy v1 registration without the field
        _authenticate_admin(client)
        workers = {w["worker_id"]: w for w in client.get("/api/agent-workers").json()["workers"]}

    assert workers["code-worker"]["max_code_concurrency"] == 2
    assert workers["home-mini"]["max_code_concurrency"] == 0


def test_register_rejects_code_capacity_on_protocol_v1(tmp_path: Path) -> None:
    """v1 heartbeats carry no cancel body, so code capacity requires v2."""
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/agent-workers/register",
            headers=_MANAGEMENT,
            json={
                "worker_id": "legacy-code",
                "runtimes": ["pi"],
                "capabilities": ["package"],
                "max_concurrency": 4,
                "max_code_concurrency": 2,
                "protocol_version": 1,
            },
        )

    assert response.status_code == 400
    assert "protocol_version" in response.json()["detail"]


def test_code_claim_injects_secrets_into_response_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    app = _make_app(tmp_path)
    _seed_code_request(app, with_secret=True)
    VaultService(app.state.job_db.path, {}).set("test-workspace", "api-token", "s3cr3t")

    with TestClient(app) as client:
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)

    assert claimed["kind"] == "code"
    manifest = claimed["manifest"]
    assert manifest["config"] == {"mode": "fast", "token": "s3cr3t"}
    assert "secret_config" not in manifest
    # The persisted manifest stays secret-free (VAULT-SECRET-001).
    with app.state.job_db._connect_read() as conn:
        stored = conn.execute(
            "select manifest_json from agent_execution_requests where kind='code'"
        ).fetchone()
    assert "s3cr3t" not in stored["manifest_json"]
    assert json.loads(stored["manifest_json"])["secret_config"] == {
        "token": {"secret_ref": "api-token"}
    }
    # The bundle endpoint serves the code bundle like any other.
    with TestClient(app) as client:
        bundle = client.get(
            f"/api/agent-executions/{claimed['execution_id']}/bundle",
            headers={"X-Agent-Worker-Token": token},
        )
    assert bundle.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(bundle.content), mode="r:gz") as tar:
        assert "node_code.py" in tar.getnames()


def test_agent_only_worker_never_claims_code(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_code_request(app)

    with TestClient(app) as client:
        token = _register_code_worker(client, max_code_concurrency=0)
        claim = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "code-worker"},
        )

    assert claim.status_code == 204


def test_heartbeat_v2_returns_cancel_body_for_code_executions(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_code_request(app)

    with TestClient(app) as client:
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token, "X-Agent-Lease-Id": claimed["lease_id"]}
        url = f"/api/agent-executions/{execution_id}/heartbeat"

        ok = client.post(url, headers=auth)
        assert ok.status_code == 200
        assert ok.json() == {"cancelled_execution_ids": []}

        with write_transaction(app.state.job_db.path) as conn:
            conn.execute("update jobs set execution_paused=1 where id='job-code-1'")
        cancelled = client.post(url, headers=auth)
        assert cancelled.status_code == 200
        assert cancelled.json() == {"cancelled_execution_ids": [execution_id]}


def test_result_auth_failure_invalidates_cached_connection_token(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_code_request(app)
    with write_transaction(app.state.job_db.path) as conn:
        conn.execute("insert into external_connections(key, type) values ('cms-prod', 'cms')")
        conn.execute(
            "insert into connection_tokens(connection_key, token_ciphertext)"
            " values ('cms-prod', 'deadbeef')"
        )

    with TestClient(app) as client:
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        (app.state.settings.jobs_dir / "job-code-1").mkdir(parents=True, exist_ok=True)
        report = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps(
                    {"status": "completed", "exit_code": 0, "auth_failure_connection": "cms-prod"}
                ),
            },
            content=_empty_archive(),
        )
        assert report.status_code == 204, report.text

    with app.state.job_db._connect_read() as conn:
        cached = conn.execute(
            "select count(*) as c from connection_tokens where connection_key='cms-prod'"
        ).fetchone()
        outcome = conn.execute(
            "select outcome_json from agent_execution_requests where kind='code'"
        ).fetchone()
    assert cached["c"] == 0
    assert json.loads(outcome["outcome_json"])["auth_failure_connection"] == "cms-prod"


def _archive_with_node_log(log_lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        payload = ("\n".join(log_lines) + "\n").encode()
        info = tarfile.TarInfo("node.log")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_code_result_promotes_node_log_to_canonical_log_path(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_code_request(app)

    with TestClient(app) as client:
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        (app.state.settings.jobs_dir / "job-code-1").mkdir(parents=True, exist_ok=True)
        report = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps({"status": "completed", "exit_code": 0}),
            },
            content=_archive_with_node_log(["line-1", "line-2"]),
        )
        assert report.status_code == 204, report.text

    log_file = app.state.settings.data_dir / "logs" / "jobs" / "job-code-1-package.log"
    assert log_file.read_text(encoding="utf-8") == "line-1\nline-2\n"
    with app.state.job_db._connect_read() as conn:
        run = conn.execute(
            "select status, log_path from node_runs where job_id='job-code-1'"
        ).fetchone()
    assert run["status"] == "completed"
    assert run["log_path"] == "logs/jobs/job-code-1-package.log"


def _archive_with_files(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            payload = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_code_result_with_expected_outputs_commits_completed(tmp_path: Path) -> None:
    """Cross-end contract: a completed code result with non-empty
    expected_outputs commits as completed — the Worker-side upload queue fills
    the output_artifacts refs (worker/upload_queue.py) that the Host commit
    requires before promoting outputs (agent_completion.py)."""
    app = _make_app(tmp_path)
    _seed_code_request(app, expected_outputs=["out.json"])

    with TestClient(app) as client:
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        (app.state.settings.jobs_dir / "job-code-1").mkdir(parents=True, exist_ok=True)
        # The Worker upload queue pushes each output first (POST /api/artifacts)
        # and reports the returned ref in output_artifacts.
        upload = client.post(
            "/api/artifacts",
            headers={"X-Agent-Worker-Token": token},
            content=b"{}\n",
        )
        assert upload.status_code == 201, upload.text
        digest = upload.json()["hash"]
        assert digest == hashlib.sha256(b"{}\n").hexdigest()
        report = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps(
                    {
                        "status": "completed",
                        "exit_code": 0,
                        "output_artifacts": {"out.json": f"sha256:{digest}"},
                    }
                ),
            },
            content=_archive_with_files({"out.json": "{}\n", "node.log": "done\n"}),
        )
        assert report.status_code == 204, report.text

    job_dir = app.state.settings.jobs_dir / "job-code-1"
    assert (job_dir / "out.json").read_text(encoding="utf-8") == "{}\n"
    with app.state.job_db._connect_read() as conn:
        run = conn.execute("select status from node_runs where job_id='job-code-1'").fetchone()
        ref = conn.execute(
            "select hash from artifact_refs where job_id='job-code-1' and name='out.json'"
        ).fetchone()
    assert run["status"] == "completed"
    assert ref["hash"] == digest
