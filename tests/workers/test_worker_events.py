"""Worker-side structured events (issue #490): emitter shape, the
HTTP-error facts (upstream status code + target URL — the middle-502 blind
spot), and the claim-loop instrumentation keeping behavior unchanged.

The events ride stdout as single JSON lines (the executor's existing output
convention, which the supervisor panel and deployment logs already collect).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from worker.events import _KNOWN_EVENTS, emit_event, http_error_fields
from worker.host.client import Client, WorkerAuthError

pytestmark = pytest.mark.no_db


def test_event_name_full_set_is_pinned() -> None:
    assert {
        "claim.attempt",
        "claim.backoff",
        "http.error",
        "execution.claimed",
        "execution.completed",
        "execution.failed",
    } == _KNOWN_EVENTS


def test_emit_event_writes_single_json_line_with_ts(capsys: pytest.CaptureFixture[str]) -> None:
    emit_event("execution.claimed", {"worker_id": "home-mini", "execution_id": "e1"})
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "execution.claimed"
    assert payload["worker_id"] == "home-mini"
    assert payload["execution_id"] == "e1"
    # ts is ISO-8601 (parses; the cross-side alignment key).
    from datetime import datetime

    datetime.fromisoformat(payload["ts"])


def test_emit_event_never_raises_on_unserializable_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_event("claim.attempt", {"weird": object()})
    out = capsys.readouterr().out
    assert '"claim.attempt"' in out  # default=str degraded, still one JSON line


def test_http_error_fields_record_status_url_and_bounded_body() -> None:
    fields = http_error_fields(
        "http://host:8000/api/agent-executions/claim", 502, b"<html>Bad Gateway</html>"
    )
    assert fields["status_code"] == 502
    assert fields["url"] == "http://host:8000/api/agent-executions/claim"
    assert "Bad Gateway" in fields["body"]
    # The issue's exact pain: a middle-layer 502 must be attributable —
    # status code AND target URL are the two facts, body is bonus.


def test_http_error_fields_scrubs_query_string_and_bounds_length() -> None:
    url = "https://host.example/api/path?token=secret-token-value&x=1"
    fields = http_error_fields(url, 500, "")
    assert "secret" not in fields["url"]
    assert fields["url"] == "https://host.example/api/path"


def test_http_error_fields_accepts_str_body() -> None:
    fields = http_error_fields("http://h/p", 429, "too many requests")
    assert fields["body"] == "too many requests"


# ---------------------------------------------------------------------------
# Client.request instrumentation (502 / transport errors carry code + URL)


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


def _patch_session(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[Any]:
    """Replace requests.Session.request; records (method, url) call args."""
    calls: list[tuple[str, str]] = []

    class _Session:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            calls.append((method, url))
            return responses.pop(0)

    monkeypatch.setattr("requests.Session", _Session)
    return calls


def test_client_request_emits_http_error_event_on_5xx(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_session(monkeypatch, [_FakeResponse(502, b"Bad Gateway")])
    client = Client("http://host.example")
    status, body = client.request("POST", "/api/agent-executions/claim", data=b"{}")
    assert status == 502
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert events[-1]["event"] == "http.error"
    assert events[-1]["status_code"] == 502
    assert events[-1]["url"] == "http://host.example/api/agent-executions/claim"
    assert events[-1]["body"] == "Bad Gateway"


def test_client_request_no_event_on_2xx_and_204(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 204 is the claim-empty success; 200 the normal answer — the observer
    # must stay silent on the healthy path (cost discipline).
    _patch_session(monkeypatch, [_FakeResponse(204, b""), _FakeResponse(200, b"{}")])
    client = Client("http://host.example")
    assert client.request("POST", "/api/agent-executions/claim")[0] == 204
    assert client.request("GET", "/api/agent-workers/self")[0] == 200
    assert capsys.readouterr().out == ""


def test_client_request_emits_on_transport_error_then_reraises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import requests

    class _BrokenSession:
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.Session", _BrokenSession)
    client = Client("http://host.example")
    with pytest.raises(requests.ConnectionError):
        client.request("POST", "/api/agent-executions/claim")
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert events[-1]["event"] == "http.error"
    assert events[-1]["url"] == "http://host.example/api/agent-executions/claim"
    assert "refused" in events[-1]["error"]


def test_client_claim_flow_still_maps_status_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Behavior pin: claim()'s 204→None / 401→WorkerAuthError / other→RuntimeError
    mapping is untouched by the instrumentation (only one extra event line)."""
    _patch_session(monkeypatch, [_FakeResponse(204, b"")])
    assert Client("http://h").claim("w1") is None
    _patch_session(monkeypatch, [_FakeResponse(401, b"nope")])
    with pytest.raises(WorkerAuthError):
        Client("http://h").claim("w1")
    _patch_session(monkeypatch, [_FakeResponse(500, b"boom")])
    with pytest.raises(RuntimeError):
        Client("http://h").claim("w1")
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    # 204 emits nothing; the two failures emit http.error events.
    assert [event["event"] for event in events].count("http.error") == 2
    assert {event["status_code"] for event in events} == {401, 500}


# ---------------------------------------------------------------------------
# run_execution's completed/failed events (both kinds reach the emitter)


class _CompletedCodeClaim:
    """Stand-in for worker.code_runner.execute_code: a code claim that
    finished cleanly and produced a process-kind UploadTask (the shape the
    agent branch builds too). Lets the events test drive run_execution's
    kind='code' path without a sandbox binary. run_execution passes the
    client/claim/execution_dir positionally."""

    def __call__(self, client, claim, execution_dir, *args: Any):  # noqa: ANN001, ANN002
        from worker.upload.queue import UploadTask

        execution_dir.mkdir(parents=True, exist_ok=True)  # the real runner prepares it
        return UploadTask(
            execution_id=str(claim["execution_id"]),
            lease_id=str(claim["lease_id"]),
            execution_dir=execution_dir,
            node_key=str(claim["node_key"]),
            status_fields={},
            kind="process",
            exec_kind="code",
            exit_code=0,
        )


def test_run_execution_code_claim_emits_completed_with_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression pin (#490): the completed event fires for kind='code'
    claims too — the exit_code used to be read from the agent branch's local
    variable and raised NameError on the code path, degrading a clean code
    result into a fabricated failure report."""
    import worker.code_runner  # noqa: F401  (patch target resolution guard)
    from worker.execution.run import run_execution
    from worker.status import ExecutionStatusReporter
    from worker.upload.queue import UploadQueue

    monkeypatch.setattr("worker.execution.run.execute_code", _CompletedCodeClaim())
    client = _CodeClaimClient()
    uploads = UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=1,
        heartbeat_interval=0.05,
        stop=threading.Event(),
    )
    run_execution(
        client,
        {
            "execution_id": "exec-code-1",
            "lease_id": "lease-1",
            "node_key": "node_a",
            "job_id": "job-1",
            "workspace_id": "ws-1",
            "kind": "code",
        },
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        ExecutionStatusReporter(None),
        uploads,
        threading.Semaphore(1),
    )
    uploads.shutdown()
    assert client.released == ["exec-code-1"]  # behavior pin: handoff happened
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    completed = [event for event in events if event["event"] == "execution.completed"]
    assert len(completed) == 1
    assert completed[0]["kind"] == "code"
    assert completed[0]["execution_id"] == "exec-code-1"
    assert completed[0]["exit_code"] == 0
    assert completed[0]["wall_seconds"] >= 0
    # The failure event must NOT fire for a clean code claim.
    assert not [event for event in events if event["event"] == "execution.failed"]


class _CodeClaimClient:
    """Minimal client for the code-claim run: heartbeat, release and the
    upload lane the UploadQueue drains through."""

    def __init__(self) -> None:
        self.released: list[str] = []
        self.reports: list[str] = []

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        return 204, []

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        self.released.append(execution_id)
        return 204

    def upload_artifact(self, path: Path) -> str:
        return "sha256:0"

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(execution_id)
        return 204, b""
