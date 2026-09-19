from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.db.transaction import write_transaction
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    claim,
    empty_archive,
    issue_scoped_token,
    make_app,
    register,
    seed_request,
)

_seed_request = seed_request

_CSRF = {"x-agent-legion-request": "1"}


_authenticate_admin = authenticate_admin
_make_app = make_app
_issue_scoped_token = issue_scoped_token
_register = register
_claim = claim
_empty_archive = empty_archive


def test_agent_worker_register_and_claim_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": credential},
            json={
                "worker_id": "home-mini",
                "name": "Home Mac mini",
                "runtimes": ["rust"],
                "max_concurrency": 1,
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "runtimes must contain pi and/or velites"


def test_agent_worker_register_accepts_empty_runtimes_for_code_only_worker(
    tmp_path: Path,
) -> None:
    """issue #254：全部 agent runtime 停用的 code-only Worker 允许空 runtimes 注册。"""
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": credential},
            json={
                "worker_id": "code-only-1",
                "runtimes": [],
                "capabilities": ["generate"],
                "max_concurrency": 1,
                "max_code_concurrency": 2,
                "protocol_version": 3,
            },
        )
        token = response.json()["worker_token"]
        worker = client.get("/api/agent-workers/self", headers={"X-Agent-Worker-Token": token})

    assert response.status_code == 201, response.text
    assert worker.status_code == 200
    assert worker.json()["runtimes"] == []
    assert worker.json()["max_code_concurrency"] == 2


def test_worker_can_read_only_its_own_status_with_issued_token(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
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
    with write_transaction(app.state.job_db.dsn_identity) as conn:
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
        _authenticate_admin(client)
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


def test_claim_ignores_worker_capability_mismatch(tmp_path: Path) -> None:
    """Issue #284: capabilities no longer gate claims — a Worker that never
    declared the node's capability still claims when runtime and the model
    allowlist match."""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
        wrong_capability = _register(
            client, capabilities=["review"], models=[{"provider": "gateway", "model": "test-model"}]
        )["worker_token"]
        response = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": wrong_capability},
            json={"worker_id": "home-mini"},
        )
        assert response.status_code == 200


def test_claim_requires_matching_worker_model(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        for bad_id in ("has.dot", "has space", "", "x" * 65):
            response = client.post(
                "/api/agent-workers/register",
                headers={"X-Agent-Worker-Register-Token": credential},
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
            headers={"X-Agent-Worker-Register-Token": credential},
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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


def test_worker_online_flag_tracks_last_seen(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
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


def test_list_workers_carries_configured_console_url(tmp_path: Path, monkeypatch) -> None:
    # 主控制台「打开 Worker 控制台」入口：列表响应随带部署级兜底地址
    # （AGENT_LEGION_WORKER_CONSOLE_URL），尚无 Worker 注册时也可用。
    monkeypatch.setenv("AGENT_LEGION_WORKER_CONSOLE_URL", "http://127.0.0.1:8789")
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
        body = client.get("/api/agent-workers").json()

    assert body["console_url"] == "http://127.0.0.1:8789"
    assert body["workers"] == []


def test_presence_sync_records_claim_switch_and_claim_implies_enabled(tmp_path: Path) -> None:
    # v83：Worker 每次状态同步上报 claim 开关；未上报前为 None（旧 Worker），
    # 上报 False 后主控制台可区分「在线·未领取」；一次 claim 轮询即回到 True。
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
        token = _register(client)["worker_token"]
        auth = {"X-Agent-Worker-Token": token}
        assert client.get("/api/agent-workers").json()["workers"][0]["claim_enabled"] is None

        presence = client.post(
            "/api/agent-workers/self/presence", headers=auth, json={"claim_enabled": False}
        )
        assert presence.status_code == 200, presence.text
        assert presence.json()["claim_enabled"] is False
        assert presence.json()["worker_id"] == "home-mini"
        assert client.get("/api/agent-workers").json()["workers"][0]["claim_enabled"] is False

        _claim(client, token)
        assert client.get("/api/agent-workers").json()["workers"][0]["claim_enabled"] is True

        # 未认证的上报被拒：状态只能由 Worker 自己用 worker token 写。
        assert (
            client.post(
                "/api/agent-workers/self/presence", json={"claim_enabled": True}
            ).status_code
            == 401
        )


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
        _authenticate_admin(client)
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
