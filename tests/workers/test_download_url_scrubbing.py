from __future__ import annotations

import hashlib
import json
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests
import urllib3.exceptions

from server.app.agent_broker.code_dispatch import build_code_bundle
from shared.material_cache import MaterializeError
from shared.resource_manifest import SHARED_RESOURCE_CLAIM_FORMAT, resource_manifest_hash
from tests.workers.execution_run_testlib import FakeClient
from worker.execution.run import run_execution
from worker.executor_log import PanelLogSinks, events_log_path, executor_log_path
from worker.material_fetch import materialize_claim_material
from worker.resource_fetch import materialize_claim_shared_resources
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = b"data"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
SIGNED_URL = (
    "https://s3.test/shared-resources/file"
    "?X-Amz-Credential=credential-value&X-Amz-Signature=signature-value&sig=sig-token"
)
SECRETS = (
    SIGNED_URL,
    "X-Amz-Credential",
    "credential-value",
    "X-Amz-Signature",
    "signature-value",
    "sig=sig-token",
)


def _single_manifest() -> dict[str, Any]:
    return {
        "runtime_context": {
            "material": {
                "material_id": "mat-1",
                "filename": "data.txt",
                "content_type": "text/plain",
                "size_bytes": len(PAYLOAD),
                "content_hash": SHA256,
                "download_url": SIGNED_URL,
            }
        }
    }


def _bundle_manifest() -> dict[str, Any]:
    return {
        "runtime_context": {
            "material": {
                "material_id": "bundle-1",
                "kind": "bundle",
                "entries": [
                    {
                        "material_id": "mat-1",
                        "path": "data.txt",
                        "size_bytes": len(PAYLOAD),
                        "content_hash": SHA256,
                        "download_url": SIGNED_URL,
                    }
                ],
            }
        }
    }


def _resource_manifest() -> dict[str, Any]:
    files = [
        {
            "path": "data.txt",
            "sha256": SHA256,
            "size_bytes": len(PAYLOAD),
            "content_type": "text/plain",
            "download_url": SIGNED_URL,
        }
    ]
    return {
        "node_key": "prepare",
        "shared_resources": ["rules"],
        "runtime_context": {
            "job": {
                "id": "job-1",
                "workflow_definition_snapshot_json": json.dumps(
                    {"nodes": {"prepare": {"shared_resources": ["rules"]}}}
                ),
            },
            "shared_resources": {
                "format": SHARED_RESOURCE_CLAIM_FORMAT,
                "pins": [
                    {
                        "resource_id": "rules",
                        "version": 1,
                        "manifest_hash": resource_manifest_hash(files),
                        "files": files,
                    }
                ],
            },
        },
    }


def _invoke(kind: str, execution_dir: Path) -> None:
    if kind == "single":
        materialize_claim_material(_single_manifest(), execution_dir)
    elif kind == "bundle":
        materialize_claim_material(_bundle_manifest(), execution_dir)
    else:
        materialize_claim_shared_resources(_resource_manifest(), execution_dir)


def _failure(call: Callable[[], None]) -> tuple[MaterializeError, str]:
    try:
        call()
    except MaterializeError as exc:
        return exc, traceback.format_exc()
    pytest.fail("download unexpectedly succeeded")


def _assert_scrubbed(exc: MaterializeError, rendered: str, error_type: str) -> None:
    assert error_type in str(exc)
    assert error_type in rendered
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    for secret in SECRETS:
        assert secret not in str(exc)
        assert secret not in rendered


@pytest.mark.parametrize("kind", ["single", "bundle", "resource"])
def test_connection_error_never_leaks_signed_url_through_exception_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    def fail_connection(*args, **kwargs):
        raise requests.ConnectionError(f"connection failed for {SIGNED_URL}")

    monkeypatch.setattr("worker.presigned_download.requests.get", fail_connection)
    exc, rendered = _failure(lambda: _invoke(kind, tmp_path / kind / "exec"))
    _assert_scrubbed(exc, rendered, "ConnectionError")


class _BrokenStream:
    def __init__(self, error: BaseException):
        self.error = error

    def read(self, _size: int) -> bytes:
        raise self.error

    def close(self) -> None:
        return None


@pytest.mark.parametrize("kind", ["single", "bundle", "resource"])
@pytest.mark.parametrize("error_type", ["ProtocolError", "OSError"])
def test_stream_errors_never_leak_signed_url_through_exception_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, error_type: str
) -> None:
    if error_type == "ProtocolError":
        error: BaseException = urllib3.exceptions.ProtocolError(f"read failed for {SIGNED_URL}")
    else:
        error = OSError(f"read failed for {SIGNED_URL}")
    target = {
        "single": "worker.material_fetch._open_download",
        "bundle": "worker.bundle_fetch._open_download",
        "resource": "worker.resource_fetch._open_download",
    }[kind]
    monkeypatch.setattr(target, lambda *args, **kwargs: _BrokenStream(error))
    exc, rendered = _failure(lambda: _invoke(kind, tmp_path / kind / error_type / "exec"))
    _assert_scrubbed(exc, rendered, error_type)


def _code_claim(code_text: str) -> dict[str, Any]:
    manifest = _resource_manifest()
    manifest.update(
        {
            "kind": "code",
            "execution_id": "exec-secret",
            "workspace_id": "ws-1",
            "job_id": "job-1",
            "workflow_key": "ws-1",
            "capability": "",
            "code_hash": hashlib.sha256(code_text.encode()).hexdigest(),
            "config": {},
            "inputs": [],
            "expected_outputs": ["output.json"],
            "timeout_seconds": 30,
            "sandbox_network": False,
        }
    )
    return {
        "execution_id": "exec-secret",
        "lease_id": "lease-1",
        "node_key": "prepare",
        "job_id": "job-1",
        "workspace_id": "ws-1",
        "kind": "code",
        "bundle_url": "/bundle/code.tar.gz",
        "manifest": manifest,
    }


@pytest.mark.parametrize("error_type", ["ConnectionError", "ProtocolError"])
def test_run_event_error_and_rolling_logs_stay_free_of_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    error_type: str,
) -> None:
    code_text = "def run(job, job_dir, runtime):\n    pass\n"
    bundle = tmp_path / "code.tar.gz"
    build_code_bundle(bundle, code_text=code_text, workspace_libs_dir=REPO_ROOT / "workspace_libs")

    if error_type == "ConnectionError":

        def fail_connection(*args, **kwargs):
            raise requests.ConnectionError(f"connection failed for {SIGNED_URL}")

        monkeypatch.setattr("worker.presigned_download.requests.get", fail_connection)
    else:
        error = urllib3.exceptions.ProtocolError(f"read failed for {SIGNED_URL}")
        monkeypatch.setattr(
            "worker.resource_fetch._open_download",
            lambda *args, **kwargs: _BrokenStream(error),
        )
    monkeypatch.setattr("worker.code_runner.resolve_sandbox_binary", lambda: "velites")
    client = FakeClient(bundle)
    status = ExecutionStatusReporter(None)
    uploads = UploadQueue(
        client,
        status,
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    run_execution(
        client,
        _code_claim(code_text),
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        status,
        uploads,
        threading.Semaphore(1),
    )
    uploads.shutdown()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    for secret in SECRETS:
        assert secret not in combined
    assert error_type in captured.err

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert error_type in report["error_message"]
    for secret in SECRETS:
        assert secret not in json.dumps(report)

    failed_events = [
        json.loads(line)
        for line in captured.out.splitlines()
        if line.startswith("{") and '"event": "execution.failed"' in line
    ]
    assert len(failed_events) == 1
    assert error_type in failed_events[0]["error"]
    for secret in SECRETS:
        assert secret not in json.dumps(failed_events[0])

    state = tmp_path / "state"
    sinks = PanelLogSinks(state)
    for line in combined.splitlines():
        sinks.write(line, f"[00:00:00] {line}", lambda _message: None)
    sinks.close()
    panel = executor_log_path(state).read_text(encoding="utf-8")
    events = events_log_path(state).read_text(encoding="utf-8")
    assert error_type in panel
    assert "execution.failed" in events
    for secret in SECRETS:
        assert secret not in panel
        assert secret not in events
