"""Result-endpoint streaming spool tests (issue #88): the report body is
streamed to a staging file and atomically renamed, never buffered in memory."""

from __future__ import annotations

import gzip as gzip_module
import hashlib
import io
import json
import tarfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.routes.test_agent_workers import _claim, _empty_archive, _make_app, _register
from tests.test_agent_broker import _seed_request


def test_result_rejects_oversized_streamed_body_and_cleans_staging(tmp_path: Path) -> None:
    """A chunked body has no Content-Length to gate on: the streaming spool
    enforces max_archive_bytes mid-body and reclaims its staging file."""
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
            content=iter([b"x" * 256, b"y" * 256]),
        )
        assert response.status_code == 413

        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert list(bundle_dir.glob(".result-*")) == []
        assert list(bundle_dir.glob("*.result.tar.gz")) == []

        # The claim is still alive for a valid retry.
        retry = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps({"status": "failed", "exit_code": 1}),
            },
            content=_empty_archive(),
        )
        assert retry.status_code == 204, retry.text


def test_result_rejects_stale_lease_before_spooling(tmp_path: Path) -> None:
    """A valid token with a stale lease is 409'd by the pre-spool ownership
    check — the doomed body never becomes a staging file on disk, and the
    real lease still commits afterwards."""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": "stale-lease",
                "X-Agent-Result": json.dumps({"status": "failed", "exit_code": 1}),
            },
            content=_empty_archive(),
        )
        assert response.status_code == 409

        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert list(bundle_dir.glob(".result-*")) == []
        assert list(bundle_dir.glob("*.result.tar.gz")) == []

        retry = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps({"status": "failed", "exit_code": 1}),
            },
            content=_empty_archive(),
        )
        assert retry.status_code == 204, retry.text


def test_result_commit_failure_reclaims_staging_file(tmp_path: Path, monkeypatch) -> None:
    """Spool succeeds but the commit fails (e.g. the lease was lost between
    the pre-check and the commit): the route's finally must reclaim the
    staging file (discard_staged_result)."""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    def _fail_commit(*args, **kwargs) -> None:
        raise HTTPException(status_code=409, detail="execution is no longer owned")

    monkeypatch.setattr("server.app.routes.agent_workers.commit_agent_result", _fail_commit)

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
            content=_empty_archive(),
        )
        assert response.status_code == 409

        bundle_dir = Path(app.state.agent_broker.bundle_dir)
        assert list(bundle_dir.glob(".result-*")) == []
        assert list(bundle_dir.glob("*.result.tar.gz")) == []


def test_result_streams_large_archive_to_disk_byte_exact(tmp_path: Path) -> None:
    """The streamed spool + atomic rename must preserve the archive byte
    content exactly; verified through the promoted run_dir events file."""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    line = (
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "provider": "gateway",
                    "model": "test-model",
                    "usage": {"input": 1, "output": 1, "cacheRead": 0},
                },
            }
        )
        + "\n"
    )
    events = (line * 8000).encode()  # >1 MiB of valid event lines
    assert len(events) > 1024 * 1024
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("runs/generate/worker/events.jsonl")
        info.size = len(events)
        tar.addfile(info, io.BytesIO(events))

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        (app.state.settings.jobs_dir / "job-1").mkdir(parents=True, exist_ok=True)
        report = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": json.dumps(
                    {"status": "completed", "run_dir": "runs/generate/worker"}
                ),
            },
            content=buffer.getvalue(),
        )
        assert report.status_code == 204, report.text

    bundle_dir = Path(app.state.agent_broker.bundle_dir)
    assert list(bundle_dir.glob(".result-*")) == []
    run_dir = app.state.settings.data_dir / "jobs" / "job-1" / "runs" / "generate" / "worker"
    plain = run_dir / "events.jsonl"
    if plain.is_file():
        promoted = plain.read_bytes()
    else:
        promoted = gzip_module.decompress((run_dir / "events.jsonl.gz").read_bytes())
    assert hashlib.sha256(promoted).hexdigest() == hashlib.sha256(events).hexdigest()
