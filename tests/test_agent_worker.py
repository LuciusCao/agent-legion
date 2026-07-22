"""Unit tests for the Agent Worker client supervisor (scripts/agent_worker.py)."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from scripts import agent_worker
from server.app.agent_bundle import build_agent_bundle


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


def _run_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeClient
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
