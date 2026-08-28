"""Unit tests for the Agent Worker executor (worker/executor.py).

Host 协议面（claim/heartbeat/registration 协议）、supervisor main() 治理
（热更/退避/撤销）、work-root 清理与状态文件读写。``run_execution`` 的单执行
生命周期（含 #203 pending-marker claim 语义）拆到
tests/workers/test_execution_run.py（测试文件行数预算）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import requests

from worker import executor as agent_worker
from worker.process_lifecycle import terminate
from worker.registration.retry import register_with_retry
from worker.status import ExecutionStatusReporter, read_runtime_status


def _claim(execution_id: str = "exec-1") -> dict:
    return {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


class FakeClient:
    """In-memory stand-in for agent_worker.Client.

    构造参数仍是 bundle path（main() 系列传一个不存在的路径即可——它们
    只打桩 claim/register/get_self，从不下载）。"""

    def __init__(
        self, bundle: Path, *, heartbeat_status: int = 204, release_status: int = 204
    ) -> None:
        self._bundle = bundle
        self._heartbeat_status = heartbeat_status
        self._release_status = release_status
        self.heartbeats = 0
        self.heartbeat_lease_ids: list[str] = []
        self.reports: list[dict] = []
        self.report_lease_ids: list[str] = []
        self.release_calls = 0
        self.uploads: dict[str, bytes] = {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        self.uploads[hashlib.sha256(data).hexdigest()] = data
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    def get_self(self) -> dict:
        return {
            "worker_id": "w1",
            "name": "Worker 1",
            "revoked": False,
            "online": True,
        }

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        self.heartbeat_lease_ids.append(lease_id)
        return self._heartbeat_status, []

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        self.release_calls += 1
        return self._release_status

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        self.reports.append(metadata)
        self.report_lease_ids.append(lease_id)
        return 204, b""


def _write_executable(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def test_clean_work_root_removes_dirs_keeps_files(tmp_path: Path) -> None:
    (tmp_path / "exec-stale").mkdir()
    (tmp_path / "note.txt").write_text("keep", encoding="utf-8")
    agent_worker.clean_work_root(tmp_path)
    assert not (tmp_path / "exec-stale").exists()
    assert (tmp_path / "note.txt").is_file()


def test_sweep_stale_executions_removes_only_old_dirs(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    old = work_root / "old-exec"
    fresh = work_root / "fresh-exec"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    events = old / "events.jsonl"
    events.write_text("{}", encoding="utf-8")
    past = time.time() - 25 * 3600
    # Staleness is judged by the newest mtime in the subtree, so the whole
    # tree must be backdated for the dir to count as stale.
    os.utime(events, (past, past))
    os.utime(old, (past, past))
    agent_worker.sweep_stale_executions(work_root)
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_executions_keeps_dir_with_fresh_nested_file(tmp_path: Path) -> None:
    # A long-running execution's top-level dir mtime freezes at extraction
    # time while runtime writes keep landing in nested dirs (job/runs/...);
    # the sweep must not rmtree it just for outliving max_age_seconds.
    work_root = tmp_path / "work"
    running = work_root / "long-exec"
    run_dir = running / "job" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.write_text("{}", encoding="utf-8")
    past = time.time() - 25 * 3600
    os.utime(running, (past, past))
    os.utime(running / "job", (past, past))
    os.utime(run_dir.parent, (past, past))
    os.utime(run_dir, (past, past))
    agent_worker.sweep_stale_executions(work_root)
    assert running.exists()
    assert events.is_file()


def test_sweep_stale_executions_removes_fully_stale_subtree(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    stale = work_root / "stale-exec"
    run_dir = stale / "job" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.write_text("{}", encoding="utf-8")
    past = time.time() - 25 * 3600
    os.utime(events, (past, past))
    os.utime(run_dir, (past, past))
    os.utime(run_dir.parent, (past, past))
    os.utime(stale / "job", (past, past))
    os.utime(stale, (past, past))
    agent_worker.sweep_stale_executions(work_root)
    assert not stale.exists()


def test_sweep_stale_executions_tolerates_missing_work_root(tmp_path: Path) -> None:
    agent_worker.sweep_stale_executions(tmp_path / "missing")


def test_terminate_never_raises_on_stubborn_process() -> None:
    class StubbornProc:
        pid = 999_999_999

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("cmd", timeout or 0)

    # killpg raises ProcessLookupError (suppressed); both waits time out.
    terminate(StubbornProc(), 0.01)  # type: ignore[arg-type]


def test_terminate_kills_sigterm_ignoring_process_group(tmp_path: Path) -> None:
    script = _write_executable(
        tmp_path / "stubborn",
        "#!/usr/bin/env python3\nimport signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n",
    )
    proc = subprocess.Popen([script], start_new_session=True)
    terminate(proc, 0.2)
    assert proc.poll() is not None


def test_client_claim_raises_auth_error_on_409() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"unknown or revoked Agent Worker")  # type: ignore[method-assign]
    with pytest.raises(agent_worker.WorkerAuthError):
        client.claim("w1")


def test_client_heartbeat_returns_status() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (409, b"")  # type: ignore[method-assign]
    assert client.heartbeat("exec-1", "lease-1") == (409, [])


def test_client_heartbeat_parses_protocol_v2_cancel_body() -> None:
    # 批次 2：v2 Host 的 heartbeat 应答 200 + 取消列表；v1 的 204 无 body。
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        200,
        b'{"cancelled_execution_ids": ["exec-9", "exec-10"]}',
    )
    assert client.heartbeat("exec-1", "lease-1") == (200, ["exec-9", "exec-10"])
    client.request = lambda *a, **k: (204, b"")  # type: ignore[method-assign]
    assert client.heartbeat("exec-1", "lease-1") == (204, [])


def test_client_claim_declares_live_capacity() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(json.loads(k["data"])), (204, b""))[1]  # type: ignore[method-assign]

    assert client.claim("w1", 70) is None

    assert seen == [{"worker_id": "w1", "max_concurrency": 70}]


def test_client_claim_declares_code_capacity() -> None:
    # 批次 2：每次 poll 重声明 code 池容量（Host 记录并强制）。
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    client.request = lambda *a, **k: (seen.append(json.loads(k["data"])), (204, b""))[1]  # type: ignore[method-assign]

    assert client.claim("w1", 70, 4) is None

    assert seen == [{"worker_id": "w1", "max_concurrency": 70, "max_code_concurrency": 4}]


def test_client_registration_declares_protocol_v3_and_code_capacity() -> None:
    client = agent_worker.Client("http://unused")
    seen: list[dict] = []
    headers: dict[str, str] = {}

    def stub(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(json.loads(kwargs["data"]))
        headers.update(kwargs["headers"])
        return (
            201,
            b'{"worker_token": "tok", "host_protocol_version": 3, "allowed_workspaces": []}',
        )

    client.request = stub  # type: ignore[method-assign]

    client.register(
        {
            "worker_id": "w1",
            "runtimes": ["velites"],
            "max_concurrency": 1,
            "max_code_concurrency": 3,
        },
        ["token-a", "token-b"],
    )

    assert seen[0]["protocol_version"] == 3
    assert seen[0]["max_code_concurrency"] == 3
    # issue #35：全部 scoped token 逗号拼进同一个注册请求。
    assert headers["X-Agent-Worker-Register-Tokens"] == "token-a,token-b"


def test_client_registration_rejects_empty_token_list() -> None:
    client = agent_worker.Client("http://unused")
    with pytest.raises(agent_worker.WorkerAuthError, match="no register token"):
        client.register({"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}, [])


def test_client_registration_rejects_old_host_before_claiming() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (  # type: ignore[method-assign]
        201,
        b'{"worker_token": "old-host-token", "allowed_workspaces": []}',
    )

    with pytest.raises(agent_worker.WorkerAuthError, match="upgrade Host before Worker"):
        client.register(
            {"worker_id": "w1", "runtimes": ["pi", "velites"], "max_concurrency": 1},
            ["management-token"],
        )

    assert client.token == ""


def test_client_registration_rejects_permanent_http_errors() -> None:
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (401, b"bad token")  # type: ignore[method-assign]
    with pytest.raises(agent_worker.WorkerAuthError, match="registration rejected"):
        client.register(
            {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1},
            ["bad-token"],
        )


def test_registration_retries_transient_host_errors_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = agent_worker.Client("http://unused")
    calls = 0

    def flaky_register(config: dict, token: str) -> dict:
        nonlocal calls
        del config, token
        calls += 1
        if calls < 3:
            # The transport-level failure register_with_retry treats as
            # "Host temporarily unavailable" (requests raises RequestException
            # subclasses; arbitrary exceptions are NOT retried anymore).
            raise requests.ConnectionError("host unavailable")
        return {"worker_token": "worker-token", "workspaces": []}

    client.register = flaky_register  # type: ignore[method-assign]
    assert register_with_retry(client, {}, ["token"], threading.Event(), 0.001)
    output = capsys.readouterr().out
    assert calls == 3
    assert "retrying" in output
    assert "Traceback" not in output


def test_registration_retries_transient_http_status_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """5xx/429 answers surface as TransientHostError and stay in the retry loop."""
    client = agent_worker.Client("http://unused")
    statuses = [503, 429, 201]

    def flaky_request(*args: object, **kwargs: object) -> tuple[int, bytes]:
        del args, kwargs
        status = statuses.pop(0)
        if status == 201:
            return (status, b'{"worker_token": "tok", "host_protocol_version": 3}')
        return (status, b"temporarily unavailable")

    client.request = flaky_request  # type: ignore[method-assign]
    config = {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}
    assert register_with_retry(client, config, ["token"], threading.Event(), 0.001)
    output = capsys.readouterr().out
    assert "retrying" in output
    assert "HTTP 503" in output


def test_registration_unexpected_client_error_crashes_loudly() -> None:
    """A non-retriable unexpected status (e.g. 404) must not enter the loop."""
    client = agent_worker.Client("http://unused")
    client.request = lambda *a, **k: (404, b"not found")  # type: ignore[method-assign]
    config = {"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1}
    with pytest.raises(RuntimeError, match="HTTP 404"):
        register_with_retry(client, config, ["token"], threading.Event(), 0.001)


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

    assert agent_worker.runtime_controls.load_claim_controls(config_path) == (7, False)

    config["max_concurrency"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="最大并发数"):
        agent_worker.runtime_controls.load_claim_controls(config_path)


def test_load_claim_controls_defaults_to_disabled(tmp_path: Path) -> None:
    config_path = _write_main_config(tmp_path)

    assert agent_worker.runtime_controls.load_claim_controls(config_path) == (1, False)


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
    monkeypatch.setattr(agent_worker, "Client", lambda host, **kwargs: fake)
    fake.register = lambda config, token: {"worker_token": "worker-token", "workspaces": []}  # type: ignore[attr-defined]
    # main() 的启动预检会探测 PATH 上的 runtime 二进制；测试与真实机器环境
    # 无关，统一打桩为全部存在（预检自身的用例单独覆盖）。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/bin/{binary}")
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

    def flaky_claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls <= 3:
            raise urllib.error.URLError("connection refused")
        return None

    fake.claim = flaky_claim  # type: ignore[attr-defined]
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
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

    def no_work(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
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

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict:
        nonlocal claim_calls
        claim_calls += 1
        execution_id = f"exec-{claim_calls}"
        releases[execution_id] = threading.Event()
        return _claim(execution_id)

    def block_execution(  # type: ignore[no-untyped-def]
        client,
        claimed,
        work_root,
        environment,
        interval,
        stop,
        grace,
        status,
        uploads,
        download_slots,
    ):
        while not stop.is_set() and not releases[claimed["execution_id"]].wait(0.01):
            pass

    fake.claim = claim  # type: ignore[attr-defined]
    monkeypatch.setattr(agent_worker, "run_execution", block_execution)
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
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

    def revoked(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        raise agent_worker.WorkerAuthError("HTTP 409: unknown or revoked")

    fake.claim = revoked  # type: ignore[attr-defined]
    thread, _, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
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


def test_runtime_status_includes_worker_authenticated_remote_state(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    reporter = ExecutionStatusReporter(path)
    reporter.set_remote(
        {
            "host_reachable": True,
            "registered": True,
            "connected": True,
            "host_worker": {"worker_id": "w1"},
            "connection_error": None,
        }
    )

    runtime = read_runtime_status(path)

    assert runtime["remote"]["host_reachable"] is True
    assert runtime["remote"]["host_worker"]["worker_id"] == "w1"


def test_read_runtime_status_returns_empty_for_dead_writer(tmp_path: Path) -> None:
    path = tmp_path / "current_executions.json"
    path.write_text(
        json.dumps({"pid": 99999999, "executions": {"exec-1": {"execution_id": "exec-1"}}}),
        encoding="utf-8",
    )
    assert read_runtime_status(path) == {"executions": [], "remote": {}}


def test_read_runtime_status_returns_empty_for_corrupt_or_missing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "current_executions.json"
    path.write_text("not json", encoding="utf-8")
    assert read_runtime_status(path) == {"executions": [], "remote": {}}
    assert read_runtime_status(tmp_path / "missing.json") == {"executions": [], "remote": {}}


def test_read_runtime_status_sorts_executions_by_started_at(tmp_path: Path) -> None:
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
    assert [item["execution_id"] for item in read_runtime_status(path)["executions"]] == [
        "a",
        "b",
    ]
