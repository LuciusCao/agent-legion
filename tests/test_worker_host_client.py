"""Unit tests for the Worker Host client (worker/host_client.py + host_transfer.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from worker.host_client import Client, WorkerAuthError


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "out.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    return sleeps


def test_upload_artifact_succeeds_first_try(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fake_request(*args, **kwargs):
        calls["n"] += 1
        return 201, json.dumps({"hash": "abc"}).encode()

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert calls["n"] == 1


def test_upload_artifact_retries_5xx_with_backoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    responses = iter([(500, b"err"), (502, b"err"), (201, json.dumps({"hash": "abc"}).encode())])
    monkeypatch.setattr(Client, "request", lambda *a, **k: next(responses))
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert sleeps == [1.0, 2.0]


def test_upload_artifact_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(Client, "request", lambda *a, **k: (500, b"err"))
    with pytest.raises(RuntimeError, match="artifact upload failed: HTTP 500"):
        Client("http://host").upload_artifact(_artifact(tmp_path))
    assert len(sleeps) == 2  # 3 attempts, 2 backoff sleeps


def test_upload_artifact_does_not_retry_4xx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    monkeypatch.setattr(Client, "request", lambda *a, **k: (413, b"too large"))
    with pytest.raises(RuntimeError, match="artifact upload failed: HTTP 413"):
        Client("http://host").upload_artifact(_artifact(tmp_path))
    assert sleeps == []


def test_upload_artifact_retries_connection_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sleeps = _patch_sleep(monkeypatch)
    responses = iter(
        [
            requests.ConnectionError("connection reset"),
            (201, json.dumps({"hash": "abc"}).encode()),
        ]
    )

    def fake_request(*args, **kwargs):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(Client, "request", fake_request)
    assert Client("http://host").upload_artifact(_artifact(tmp_path)) == "sha256:abc"
    assert sleeps == [1.0]


def test_upload_artifact_reports_last_connection_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_sleep(monkeypatch)

    def fake_request(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(Client, "request", fake_request)
    with pytest.raises(RuntimeError, match="artifact upload failed: .*connection refused"):
        Client("http://host").upload_artifact(_artifact(tmp_path))


def test_get_self_uses_worker_token_and_returns_own_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(self, method: str, path: str, **kwargs) -> tuple[int, bytes]:
        seen.append((method, path))
        return 200, b'{"worker_id":"worker-1","name":"Worker 1"}'

    monkeypatch.setattr(Client, "request", fake_request)

    assert Client("http://host", "worker-token").get_self()["worker_id"] == "worker-1"
    assert seen == [("GET", "/api/agent-workers/self")]


def test_get_self_rejects_invalid_worker_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: (401, b"invalid token"))

    with pytest.raises(WorkerAuthError):
        Client("http://host", "bad-token").get_self()


def test_get_ops_metrics_uses_worker_scoped_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_request(self, method: str, path: str, **kwargs) -> tuple[int, bytes]:
        seen.append((method, path))
        return 200, b'{"granularity":"6h","buckets":[]}'

    monkeypatch.setattr(Client, "request", fake_request)

    payload = Client("http://host", "worker-token").get_ops_metrics("6h")

    assert payload["granularity"] == "6h"
    assert seen == [("GET", "/api/agent-workers/self/metrics?granularity=6h")]


def test_get_ops_metrics_rejects_invalid_worker_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: (401, b"invalid token"))

    with pytest.raises(WorkerAuthError):
        Client("http://host", "bad-token").get_ops_metrics("24h")
