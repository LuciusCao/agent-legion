"""Route tests for the kind='code' execution protocol surface (batch 2).

Split from ``test_agent_workers.py`` (test-file size limit, zero test
changes moved): dual pools registration, secret injection into the claim
manifest, the heartbeat cancel body, the auth-failure commit hook,
node.log promotion, expected-outputs commit, and the result-stage profile
timings. The agent-path registration/claim/heartbeat/result tests stay in
the parent file.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app.agent_broker import AgentExecutionRequest
from server.app.db.transaction import write_transaction
from server.app.services.vault import VaultService
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    empty_archive,
    issue_scoped_token,
    make_app,
    register,
)

_authenticate_admin = authenticate_admin
_make_app = make_app
_issue_scoped_token = issue_scoped_token
_register = register
_empty_archive = empty_archive


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
    with write_transaction(app.state.job_db.dsn_identity) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, 'test-workspace', 'question', %s)",
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
        headers={"X-Agent-Worker-Register-Token": _issue_scoped_token(client)},
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
    claims = response.json()["claims"]  # #547 batch wrapper
    assert len(claims) == 1
    return dict(claims[0])


def test_register_roundtrips_code_capacity(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": credential},
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
    VaultService(app.state.job_db.dsn_identity, {}).set("test-workspace", "api-token", "s3cr3t")

    with TestClient(app) as client:
        _authenticate_admin(client)
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
    assert manifest["runtime_context"]["job_batch"] is None
    assert "runtime_context" not in json.loads(stored["manifest_json"])
    # The bundle endpoint serves the code bundle like any other.
    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
        _authenticate_admin(client)
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        execution_id = claimed["execution_id"]
        auth = {"X-Agent-Worker-Token": token, "X-Agent-Lease-Id": claimed["lease_id"]}
        url = f"/api/agent-executions/{execution_id}/heartbeat"

        ok = client.post(url, headers=auth)
        assert ok.status_code == 200
        assert ok.json() == {"cancelled_execution_ids": []}

        with write_transaction(app.state.job_db.dsn_identity) as conn:
            conn.execute("update jobs set execution_paused=1 where id='job-code-1'")
        cancelled = client.post(url, headers=auth)
        assert cancelled.status_code == 200
        assert cancelled.json() == {"cancelled_execution_ids": [execution_id]}


def test_result_auth_failure_invalidates_cached_connection_token(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_code_request(app)
    with write_transaction(app.state.job_db.dsn_identity) as conn:
        conn.execute("insert into external_connections(key, type) values ('cms-prod', 'cms')")
        conn.execute(
            "insert into connection_tokens(connection_key, token_ciphertext)"
            " values ('cms-prod', 'deadbeef')"
        )

    with TestClient(app) as client:
        _authenticate_admin(client)
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
        _authenticate_admin(client)
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
    the output_artifacts refs (worker/upload/queue.py) that the Host commit
    requires before promoting outputs (agent_completion.py)."""
    app = _make_app(tmp_path)
    _seed_code_request(app, expected_outputs=["out.json"])

    with TestClient(app) as client:
        _authenticate_admin(client)
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


def test_result_commit_records_stage_timings_in_profile(tmp_path, monkeypatch) -> None:
    """#521 end-to-end: a real result commit folds per-stage timings into the
    runtime profile — the unpack/artifacts/lease_write/events/mark_done chain
    runs inside commit_agent_result, so a 204 report must leave non-zero
    stage totals behind (the route-level note_result counter doubles as the
    control: it fires on the same request).

    The module-level profile singleton is shared across every app in this
    process, and any live ops-metrics sampler thread (this app's, or one a
    sibling test left behind on a shared xdist worker) drains it through
    persist_profile_sample's snapshot_and_reset — the only drain site in
    product code. Neutralize the persist during the test so the counters
    accumulate undisturbed, read inside the TestClient context, and clear
    the residue the earlier tests left."""
    from server.app.services.runtime_profile import profile

    monkeypatch.setattr(
        "server.app.services.runtime_profile.persist_profile_sample",
        lambda *args, **kwargs: None,
    )
    app = _make_app(tmp_path)
    _seed_code_request(app, expected_outputs=["out.json"])

    with TestClient(app) as client:
        # Drop any residue earlier tests left in the shared singleton.
        profile.counters.snapshot_and_reset()
        _authenticate_admin(client)
        token = _register_code_worker(client)
        claimed = _claim_code(client, token)
        (app.state.settings.jobs_dir / "job-code-1").mkdir(parents=True, exist_ok=True)
        upload = client.post(
            "/api/artifacts",
            headers={"X-Agent-Worker-Token": token},
            content=b"{}\n",
        )
        assert upload.status_code == 201, upload.text
        digest = upload.json()["hash"]
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
        deltas = profile.counters.snapshot_and_reset()

    assert deltas["result_count"] >= 1
    assert deltas["result_seconds_total"] > 0.0
    # The stage chain: all seven markers fire unconditionally on this
    # completed path (subagent review on #530: pinning only four let a
    # deleted marker's wall time silently fold into the next stage — a
    # dropped validate/artifacts_upload/events marker passed every test).
    for stage in (
        "unpack",
        "artifacts_verify",
        "validate",
        "artifacts_upload",
        "lease_write",
        "events",
        "mark_done",
    ):
        assert deltas[f"result_{stage}_seconds_total"] > 0.0, stage
