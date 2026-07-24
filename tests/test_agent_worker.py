"""Unit tests for the Agent Worker executor (worker/executor.py)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from server.app.agent_bundle import build_agent_bundle
from worker import executor as agent_worker
from worker.status import ExecutionStatusReporter, read_current_executions


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / f"bundle-{len(list(tmp_path.glob('bundle-*')))}.tar.gz"
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


def _manifest(command: list[str], *, timeout_seconds: int = 60) -> dict:
    return {
        "command_spec": {"command": command, "prompt": "do the thing"},
        "input_artifacts": {},
        "expected_outputs": ["output.json"],
        "pi": {"timeout_seconds": timeout_seconds},
    }


def _claim(execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


class FakeClient:
    """In-memory stand-in for agent_worker.Client."""

    def __init__(self, bundle: Path, *, heartbeat_status: int = 204) -> None:
        self._bundle = bundle
        self._heartbeat_status = heartbeat_status
        self.heartbeats = 0
        self.heartbeat_lease_ids: list[str] = []
        self.reports: list[dict] = []
        self.report_lease_ids: list[str] = []
        self.uploads: dict[str, bytes] = {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        self.uploads[hashlib.sha256(data).hexdigest()] = data
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def heartbeat(self, execution_id: str, lease_id: str) -> int:
        self.heartbeats += 1
        self.heartbeat_lease_ids.append(lease_id)
        return self._heartbeat_status

    def report(self, execution_id: str, lease_id: str, metadata: dict, archive: Path) -> None:
        self.reports.append(metadata)
        self.report_lease_ids.append(lease_id)


def _run(client: FakeClient, work_root: Path, shutdown: threading.Event | None = None) -> None:
    agent_worker.run_execution(
        client,
        _claim(),
        work_root,
        {},
        0.05,
        shutdown or threading.Event(),
        1,
        ExecutionStatusReporter(None),
    )


def _write_executable(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def test_run_execution_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["exit_code"] == 0
    assert "output.json" in report["output_artifacts"]
    assert client.heartbeats >= 1
    # Every heartbeat and the result report carry the claimed lease_id.
    assert client.heartbeat_lease_ids and set(client.heartbeat_lease_ids) == {"lease-1"}
    assert client.report_lease_ids == ["lease-1"]


def test_run_execution_pre_spawn_failure_reports_failed(tmp_path: Path) -> None:
    client = FakeClient(_make_bundle(tmp_path, _manifest(["true"])))

    def boom(path: str, destination: Path) -> None:
        raise urllib.error.URLError("host unreachable")

    client.download = boom  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "failed"
    assert "host unreachable" in client.reports[0]["error_message"]


def test_run_execution_model_error_with_exit_zero_reports_failed(tmp_path: Path) -> None:
    # Pi exits 0 even when the model call fails (e.g. provider 401); the
    # worker must scan its own events file and report the real error instead
    # of "completed" with no artifacts.
    event = json.dumps(
        {
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "401: Authentication Fails",
            }
        }
    )
    script = _write_executable(
        tmp_path / "fake_pi",
        f"#!/usr/bin/env python3\nprint({event!r})\n",
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "401" in report["error_message"]


def test_run_execution_heartbeat_409_kills_run_and_skips_report(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "sleeper", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])), heartbeat_status=409)
    started = time.monotonic()
    _run(client, tmp_path / "work")
    elapsed = time.monotonic() - started
    assert client.reports == []
    assert elapsed < 20, f"ownership-lost run was not killed promptly ({elapsed:.1f}s)"


def test_run_execution_transient_heartbeat_error_keeps_beating(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    calls = 0

    def flaky(execution_id: str, lease_id: str) -> int:
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            raise urllib.error.URLError("boom")
        return 500

    client.heartbeat = flaky  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "completed"


def test_run_execution_shutdown_terminates_child_and_reports_cancelled(
    tmp_path: Path,
) -> None:
    script = _write_executable(
        tmp_path / "sleeper", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n"
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    shutdown = threading.Event()
    threading.Timer(0.5, shutdown.set).start()
    started = time.monotonic()
    _run(client, tmp_path / "work", shutdown=shutdown)
    elapsed = time.monotonic() - started
    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "cancelled"
    assert elapsed < 20, f"shutdown was not bounded ({elapsed:.1f}s)"


def test_run_execution_replaces_stale_execution_dir(tmp_path: Path) -> None:
    stale = tmp_path / "work" / "exec-1"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("old", encoding="utf-8")
    script = _write_executable(
        tmp_path / "fake_pi",
        '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("output.json").write_text("{}", encoding="utf-8")\n',
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    _run(client, tmp_path / "work")
    assert client.reports[0]["status"] == "completed"


def test_clean_work_root_removes_dirs_keeps_files(tmp_path: Path) -> None:
    (tmp_path / "exec-stale").mkdir()
    (tmp_path / "note.txt").write_text("keep", encoding="utf-8")
    agent_worker.clean_work_root(tmp_path)
    assert not (tmp_path / "exec-stale").exists()
    assert (tmp_path / "note.txt").is_file()


def test_run_execution_compresses_events_before_report(tmp_path: Path) -> None:
    # Streaming deltas are dropped so the uploaded archive (and the local
    # copy) stay small; snapshots needed by the host log renderer are kept.
    delta = json.dumps({"type": "message_update", "delta": "thinking_delta", "text": "x"})
    snapshot = json.dumps({"type": "message_end", "message": {"role": "assistant"}})
    script = _write_executable(
        tmp_path / "fake_pi",
        f"#!/usr/bin/env python3\nprint({delta!r})\nprint({snapshot!r})\n",
    )
    client = FakeClient(_make_bundle(tmp_path, _manifest([script])))
    captured: list[str] = []
    original_report = client.report

    def report_and_capture(execution_id, lease_id, metadata, archive):  # type: ignore[no-untyped-def]
        with tarfile.open(archive, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("events.jsonl"))
            extracted = tar.extractfile(member)
            assert extracted is not None
            captured.append(extracted.read().decode("utf-8"))
        original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    _run(client, tmp_path / "work")
    assert client.reports[0]["status"] == "completed"
    assert len(captured) == 1
    assert "message_end" in captured[0]
    assert "thinking_delta" not in captured[0]


def test_sweep_stale_executions_removes_only_old_dirs(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    old = work_root / "old-exec"
    fresh = work_root / "fresh-exec"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (old / "events.jsonl").write_text("{}", encoding="utf-8")
    past = time.time() - 25 * 3600
    os.utime(old, (past, past))
    agent_worker.sweep_stale_executions(work_root)
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_executions_tolerates_missing_work_root(tmp_path: Path) -> None:
    agent_worker.sweep_stale_executions(tmp_path / "missing")


def test_terminate_never_raises_on_stubborn_process() -> None:
    class StubbornProc:
        pid = 999_999_999

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("cmd", timeout or 0)

    # killpg raises ProcessLookupError (suppressed); both waits time out.
    agent_worker.terminate(StubbornProc(), 0.01)  # type: ignore[arg-type]


def test_terminate_kills_sigterm_ignoring_process_group(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "stubborn",
        "#!/usr/bin/env python3\nimport signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n",
    )
    proc = subprocess.Popen([script], start_new_session=True)
    agent_worker.terminate(proc, 0.2)
    assert proc.poll() is not None


def test_client_claim_raises_auth_error_on_409() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"unknown or revoked Agent Worker")  # type: ignore[method-assign]
    with pytest.raises(agent_worker.WorkerAuthError):
        client.claim("w1")


def test_client_revoke_posts_with_management_token() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, **kwargs: object) -> tuple[int, bytes]:
        seen.append((method, path, kwargs.get("headers") or {}))  # type: ignore[arg-type]
        return 200, b'{"worker_id": "w1", "revoked": true}'

    client.request = fake_request  # type: ignore[method-assign]
    client.revoke("w1", "management-token")

    assert seen == [
        (
            "POST",
            "/api/agent-workers/w1/revoke",
            {"X-Agent-Worker-Register-Token": "management-token"},
        )
    ]


def test_client_revoke_raises_on_error_status() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (401, b"invalid token")  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="revoke"):
        client.revoke("w1", "management-token")


def test_client_heartbeat_returns_status() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"")  # type: ignore[method-assign]
    assert client.heartbeat("exec-1", "lease-1") == 409


def test_client_heartbeat_and_report_send_lease_header() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(k.get("headers") or {}), (204, b""))[1]  # type: ignore[method-assign]
    client.heartbeat("exec-1", "lease-9")
    archive = Path(__file__)
    client.report("exec-1", "lease-9", {"status": "completed"}, archive)
    assert [call.get("X-Agent-Lease-Id") for call in seen] == ["lease-9", "lease-9"]


def _write_main_config(tmp_path: Path) -> Path:
    token_file = tmp_path / "register_token"
    token_file.write_text("management-token", encoding="utf-8")
    config = {
        "host_url": "http://unused",
        "worker_id": "w1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
        "register_token_file": str(token_file),
        "work_root": str(tmp_path / "work"),
        "poll_interval_seconds": 0.05,
        "heartbeat_interval_seconds": 0.05,
    }
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_load_claim_controls_reads_hot_fields_and_validates_types(tmp_path: Path) -> None:
    config_path = _write_main_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_concurrency": 7, "claim_enabled": False})
    config_path.write_text(json.dumps(config), encoding="utf-8")

    assert agent_worker.load_claim_controls(config_path) == (7, False)

    config["max_concurrency"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="最大并发数"):
        agent_worker.load_claim_controls(config_path)


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: FakeClient,
    config_updates: dict | None = None,
) -> tuple[threading.Thread, dict, list]:
    """Run main() in a thread with a stubbed signal module and Client."""
    handlers: dict = {}
    monkeypatch.setattr(
        agent_worker.signal,
        "signal",
        lambda sig, handler: handlers.setdefault(sig, handler),
    )
    monkeypatch.setattr(agent_worker, "Client", lambda host: fake)
    fake.register = lambda config, token: "worker-token"  # type: ignore[attr-defined]
    config_path = _write_main_config(tmp_path)
    if config_updates:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(config_updates)
        config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["agent_worker.py", "--config", str(config_path)])
    result: list[int] = []

    def target() -> None:
        result.append(agent_worker.main())

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, handlers, result


def test_main_survives_transient_claim_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0

    def flaky_claim(worker_id: str) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls <= 3:
            raise urllib.error.URLError("connection refused")
        return None

    fake.claim = flaky_claim  # type: ignore[attr-defined]
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake)
    deadline = time.monotonic() + 10
    while claim_calls < 5 and time.monotonic() < deadline:
        time.sleep(0.02)
    handlers[agent_worker.signal.SIGTERM]()
    thread.join(timeout=10)
    assert claim_calls >= 5, "supervisor died on a transient claim error"
    assert result == [0]


def test_main_hot_reloads_claim_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0

    def no_work(worker_id: str) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        return None

    fake.claim = no_work  # type: ignore[attr-defined]
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": False})
    time.sleep(0.2)
    assert claim_calls == 0

    config_path = tmp_path / "worker.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["claim_enabled"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")
    deadline = time.monotonic() + 2
    while claim_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    handlers[agent_worker.signal.SIGTERM]()
    thread.join(timeout=10)
    assert claim_calls > 0
    assert result == [0]


def test_main_hot_resizes_capacity_without_cancelling_active_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    releases: dict[str, threading.Event] = {}

    def claim(worker_id: str) -> dict:
        nonlocal claim_calls
        claim_calls += 1
        execution_id = f"exec-{claim_calls}"
        releases[execution_id] = threading.Event()
        return _claim(execution_id)

    def block_execution(  # type: ignore[no-untyped-def]
        client, claimed, work_root, environment, interval, stop, grace, status
    ):
        while not stop.is_set() and not releases[claimed["execution_id"]].wait(0.01):
            pass

    fake.claim = claim  # type: ignore[attr-defined]
    monkeypatch.setattr(agent_worker, "run_execution", block_execution)
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake)
    deadline = time.monotonic() + 2
    while claim_calls < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert claim_calls == 1

    config_path = tmp_path / "worker.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_concurrency"] = 3
    config_path.write_text(json.dumps(config), encoding="utf-8")
    deadline = time.monotonic() + 2
    while claim_calls < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert claim_calls == 3

    config["max_concurrency"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    releases["exec-1"].set()
    time.sleep(0.2)
    assert claim_calls == 3

    handlers[agent_worker.signal.SIGTERM]()
    thread.join(timeout=10)
    assert result == [0]


def test_main_exits_cleanly_on_revoked_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient(tmp_path / "unused.tar.gz")

    def revoked(worker_id: str) -> dict | None:
        raise agent_worker.WorkerAuthError("HTTP 409: unknown or revoked")

    fake.claim = revoked  # type: ignore[attr-defined]
    thread, _, result = _run_main(monkeypatch, tmp_path, fake)
    thread.join(timeout=10)
    assert result == [2]
    assert "re-register required" in capsys.readouterr().out


def test_status_reporter_tracks_concurrent_executions(tmp_path: Path) -> None:
    path = tmp_path / "current_executions.json"
    reporter = ExecutionStatusReporter(path)
    reporter.start(
        "exec-1",
        job_id="job-1",
        node_key="node_a",
        workflow_key="wf",
        agent_id="pi",
        run_dir="/tmp/1",
    )
    reporter.start(
        "exec-2",
        job_id="job-2",
        node_key="node_b",
        workflow_key="wf",
        agent_id="pi",
        run_dir="/tmp/2",
    )
    reporter.set_phase("exec-1", "running")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["executions"]["exec-1"]["phase"] == "running"
    assert payload["executions"]["exec-1"]["started_at"]
    assert payload["executions"]["exec-2"]["phase"] == "claimed"
    reporter.finish("exec-1")
    reporter.finish("exec-2")
    assert json.loads(path.read_text(encoding="utf-8"))["executions"] == {}


def test_status_reporter_without_env_path_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_WORKER_STATUS_FILE", raising=False)
    reporter = ExecutionStatusReporter.from_env()
    reporter.start(
        "exec-1",
        job_id="job-1",
        node_key="node_a",
        workflow_key="wf",
        agent_id="pi",
        run_dir="/tmp/1",
    )
    reporter.set_phase("exec-1", "running")
    reporter.finish("exec-1")
    assert not (tmp_path / "current_executions.json").exists()


def test_read_current_executions_returns_empty_for_dead_writer(tmp_path: Path) -> None:
    path = tmp_path / "current_executions.json"
    path.write_text(
        json.dumps({"pid": 99999999, "executions": {"exec-1": {"execution_id": "exec-1"}}}),
        encoding="utf-8",
    )
    assert read_current_executions(path) == []


def test_read_current_executions_returns_empty_for_corrupt_or_missing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "current_executions.json"
    path.write_text("not json", encoding="utf-8")
    assert read_current_executions(path) == []
    assert read_current_executions(tmp_path / "missing.json") == []


def test_read_current_executions_sorts_by_started_at(tmp_path: Path) -> None:
    path = tmp_path / "current_executions.json"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "executions": {
                    "b": {"execution_id": "b", "started_at": "2026-07-23T02:00:00+00:00"},
                    "a": {"execution_id": "a", "started_at": "2026-07-23T01:00:00+00:00"},
                },
            }
        ),
        encoding="utf-8",
    )
    assert [item["execution_id"] for item in read_current_executions(path)] == ["a", "b"]


def test_run_execution_publishes_status_and_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    status_path = tmp_path / "state" / "current_executions.json"
    status_path.parent.mkdir(parents=True)
    captured = tmp_path / "captured.json"
    script = _write_executable(
        tmp_path / "fake_pi",
        "#!/usr/bin/env python3\n"
        "import shutil, sys\n"
        "from pathlib import Path\n"
        "shutil.copy(sys.argv[1], sys.argv[2])\n"
        "Path('output.json').write_text('{}', encoding='utf-8')\n",
    )
    client = FakeClient(
        _make_bundle(tmp_path, _manifest([script, str(status_path), str(captured)]))
    )
    reporter = ExecutionStatusReporter(status_path)
    agent_worker.run_execution(
        client,
        _claim(),
        tmp_path / "work",
        {},
        0.05,
        threading.Event(),
        1,
        reporter,
    )
    snapshot = json.loads(captured.read_text(encoding="utf-8"))
    assert snapshot["executions"]["exec-1"]["phase"] == "running"
    assert snapshot["executions"]["exec-1"]["node_key"] == "node_a"
    assert snapshot["executions"]["exec-1"]["started_at"]
    assert read_current_executions(status_path) == []
