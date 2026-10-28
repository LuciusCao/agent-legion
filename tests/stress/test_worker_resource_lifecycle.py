from __future__ import annotations

import fcntl
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.stress import worker_resource_restart as restart
from scripts.stress.worker_resource_identity import ContractViolationError
from scripts.stress.worker_resource_measurement import summarize_samples
from scripts.stress.worker_resource_reporting import finalize_measurement_run
from scripts.stress.worker_resource_restart import (
    ExecutionReadinessError,
    enable_claim,
    require_current_registration,
    require_pending_restart,
)
from scripts.stress.worker_resource_workload import MeasurementRun, parse_args, write_run_report

pytestmark = pytest.mark.no_db


def _expected() -> dict:
    return {
        "worker_id": "resource-measurement-worker",
        "workspace_ids": {"resource_agent", "resource_code", "resource_mixed", "resource_upload"},
        "runtime": "velites",
        "agent_concurrency": 1,
        "code_concurrency": 1,
    }


def _status(pid: int, *, registered: bool = True, running: bool = True) -> dict:
    host = {
        "worker_id": "resource-measurement-worker",
        "allowed_workspaces": [
            "resource_agent",
            "resource_code",
            "resource_mixed",
            "resource_upload",
        ],
        "runtimes": ["velites"],
        "max_concurrency": 1,
        "max_code_concurrency": 1,
        "registered_at": "2026-01-01T00:00:00+00:00",
        "last_seen_at": f"2026-01-01T00:00:0{pid}+00:00",
    }
    return {
        "worker_running": running,
        "pid": pid,
        "started_at": 1767225600.0 + pid,
        "registered": registered,
        "host_worker": host if registered else None,
    }


def _host_row() -> dict:
    return _status(2)["host_worker"]


def test_stale_host_row_cannot_satisfy_restart_readiness() -> None:
    with pytest.raises(ExecutionReadinessError, match="not registered"):
        require_current_registration(
            _status(2, registered=False), [_host_row()], _status(1), _expected()
        )


def test_restart_readiness_rejects_same_pid_startup_failure_and_out_of_order_host() -> None:
    with pytest.raises(ExecutionReadinessError, match="PID did not change"):
        require_current_registration(_status(1), [_host_row()], _status(1), _expected())
    with pytest.raises(ExecutionReadinessError, match="not registered"):
        require_current_registration(
            _status(2, running=False), [_host_row()], _status(1), _expected()
        )
    with pytest.raises(ExecutionReadinessError, match="not the current executor registration"):
        require_current_registration(
            _status(2),
            [{**_host_row(), "last_seen_at": "2020-01-01T00:00:00+00:00"}],
            _status(1),
            _expected(),
        )


def test_restart_readiness_returns_current_pid_and_registration_evidence() -> None:
    current_host = {**_host_row(), "last_seen_at": "2026-01-01T00:00:03+00:00"}
    evidence = require_current_registration(_status(2), [current_host], _status(1), _expected())
    assert evidence["observed"] is True
    assert evidence["before"]["pid"] == 1
    assert evidence["after"]["pid"] == 2
    assert evidence["after"]["started_at"] == 1767225602.0
    assert evidence["host_worker"]["last_seen_at"] == "2026-01-01T00:00:03+00:00"
    assert evidence["host_worker"]["local_last_seen_at"] == "2026-01-01T00:00:02+00:00"
    assert evidence["observed_at"]
    initial = require_current_registration(_status(2), [current_host], None, _expected())
    assert initial["before"] == {"pid": None, "started_at": None}


def _pending_response(pid: int = 2, *, registered: bool = False) -> dict:
    return {
        "restarted": True,
        "config": {"disabled_runtimes": ["velites"]},
        "status": _status(pid, registered=registered),
    }


def _refused_status() -> dict:
    return {
        **_status(2, registered=False, running=False),
        "failed": "startup preflight refused disabled expected runtime",
        "exit_code": 2,
    }


def test_pending_restart_requires_new_process_and_terminal_startup_refusal() -> None:
    evidence = require_pending_restart(
        _status(1), _pending_response(), _refused_status(), "velites"
    )
    assert evidence["observed"] is True
    assert evidence["before"]["pid"] == 1
    assert evidence["after"]["pid"] == 2
    assert evidence["terminal"] == {
        "worker_running": False,
        "registered": False,
        "exit_code": 2,
        "failed": "startup preflight refused disabled expected runtime",
    }


def test_pending_restart_accepts_response_already_at_startup_refusal() -> None:
    response = _pending_response()
    response["status"] = {**_refused_status(), "pid": None}
    evidence = require_pending_restart(_status(1), response, _refused_status(), "velites")
    assert evidence["observed"] is True
    assert evidence["after"]["pid"] is None
    assert evidence["after"]["started_at"] > evidence["before"]["started_at"]


@pytest.mark.parametrize("running", [0, 1, None, "true", "false"])
def test_pending_restart_rejects_non_boolean_running_state(running: object) -> None:
    response = _pending_response()
    response["status"] = {**_refused_status(), "pid": None, "worker_running": running}
    with pytest.raises(ContractViolationError, match="new pending process"):
        require_pending_restart(_status(1), response, _refused_status(), "velites")


def test_pending_restart_rejects_old_terminal_generation_and_missing_identity() -> None:
    old_terminal = _pending_response()
    old_terminal["status"] = {**_refused_status(), "pid": None, "started_at": 1767225601.0}
    with pytest.raises(ContractViolationError, match="new pending process"):
        require_pending_restart(_status(1), old_terminal, _refused_status(), "velites")
    missing_started = _pending_response()
    missing_started["status"]["started_at"] = None
    with pytest.raises(ContractViolationError, match="process identity"):
        require_pending_restart(_status(1), missing_started, _refused_status(), "velites")
    with pytest.raises(ContractViolationError, match="process identity"):
        require_pending_restart({"pid": 1}, _pending_response(), _refused_status(), "velites")
    missing_failure = _pending_response()
    missing_failure["status"] = {**_refused_status(), "failed": None}
    with pytest.raises(ContractViolationError, match="startup refusal"):
        require_pending_restart(_status(1), missing_failure, _refused_status(), "velites")


def test_pending_restart_rejects_stale_response_and_non_refusal_states() -> None:
    with pytest.raises(ContractViolationError, match="new pending process"):
        require_pending_restart(_status(1), _pending_response(1), _refused_status(), "velites")
    with pytest.raises(ContractViolationError, match="new pending process"):
        require_pending_restart(
            _status(1), _pending_response(registered=True), _refused_status(), "velites"
        )
    for terminal in (
        _status(2),
        {**_refused_status(), "registered": True},
        {**_refused_status(), "failed": None, "exit_code": 0},
    ):
        with pytest.raises(ExecutionReadinessError, match="startup refusal"):
            require_pending_restart(_status(1), _pending_response(), terminal, "velites")


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
        return value if tz is not None else value.replace(tzinfo=None)


def _enable_claim_with_heartbeats(host_last_seen: str, local_last_seen: str) -> dict:
    status = _status(2)
    status["host_worker"]["last_seen_at"] = local_last_seen
    host_row = {**_host_row(), "last_seen_at": host_last_seen}
    run = SimpleNamespace(
        worker_id="resource-measurement-worker",
        workspaces={value: value for value in _expected()["workspace_ids"]},
        args=SimpleNamespace(agent_concurrency=1, code_concurrency=1),
        control_token="control-token",
        host_url="http://host",
        _wait_worker_process=lambda _token: None,
        _wait=lambda _description, probe: probe(),
    )

    def worker_request(method, path, token, **_kwargs):
        assert token == "control-token"
        if method == "GET":
            assert path == "/api/status"
            return SimpleNamespace(json=lambda: status)
        return SimpleNamespace()

    run._worker_request = worker_request
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"workers": [host_row]})
    session = SimpleNamespace(get=lambda _url, **_kwargs: response)
    return enable_claim(run, "control-token", session, _status(1))


@pytest.mark.parametrize("future_side", ["host", "local"])
def test_enable_claim_rejects_future_heartbeat(
    future_side: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart, "datetime", _FixedDatetime)
    future = "2026-01-01T00:00:11+00:00"
    host = future if future_side == "host" else "2026-01-01T00:00:03+00:00"
    local = future if future_side == "local" else "2026-01-01T00:00:04+00:00"
    with pytest.raises(ExecutionReadinessError, match="not the current executor registration"):
        _enable_claim_with_heartbeats(host, local)


def test_enable_claim_accepts_legal_heartbeat_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restart, "datetime", _FixedDatetime)
    evidence = _enable_claim_with_heartbeats(
        "2026-01-01T00:00:03+00:00", "2026-01-01T00:00:04+00:00"
    )
    assert evidence["observed"] is True
    observed_at = datetime.fromisoformat(evidence["observed_at"])
    assert observed_at == datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
    assert observed_at.tzinfo is UTC


class _FakeSampler:
    def __init__(self, _reader: object, raw_path: Path, **_kwargs: object) -> None:
        self.raw_path = raw_path
        self.rows: list[dict] = []

    def mark(self, iteration: str, scenario: str) -> None:
        self.iteration = iteration
        self.scenario = scenario

    def start(self) -> None:
        self.rows = [
            {
                "kind": "memory",
                "iteration": self.iteration,
                "scenario": "agent",
                "values": {"current_bytes": 1, "peak_bytes": 1, "max": "max", "stat": {}},
            },
            {
                "kind": "memory",
                "iteration": self.iteration,
                "scenario": "agent",
                "values": {"current_bytes": 2, "peak_bytes": 2, "max": "max", "stat": {}},
            },
            {
                "kind": "pids",
                "iteration": self.iteration,
                "scenario": "agent",
                "values": {"current": 1, "max": "max", "events": {"max": 0}},
            },
            {
                "kind": "pids",
                "iteration": self.iteration,
                "scenario": "agent",
                "values": {"current": 2, "max": "max", "events": {"max": 0}},
            },
            {
                "kind": "pids",
                "iteration": self.iteration,
                "scenario": "idle-registered",
                "values": {"current": 3, "max": "max", "events": {"max": 0}},
            },
        ]
        self.raw_path.write_text(
            "\n".join(json.dumps(row) for row in self.rows) + "\n", encoding="utf-8"
        )

    def stop(self) -> list[str]:
        return []

    def write_summary(self, output_path: Path) -> dict:
        summary = summarize_samples(self.rows)
        output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary


def test_outer_finalization_counts_match_result_run_report_summary_and_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    run = MeasurementRun.__new__(MeasurementRun)
    run.args = SimpleNamespace(
        jobs_per_wave=2,
        upload_concurrency=1,
        pids_interval_ms=100,
        memory_interval_ms=500,
        artifact_chars=1,
        agent_concurrency=1,
        code_concurrency=1,
        idle_seconds=0,
        job_timeout_seconds=1,
        backlog_timeout_seconds=1,
        s3_port=9000,
    )
    run.work_dir = tmp_path / "work"
    run.output_dir = tmp_path / "results"
    run.velites = tmp_path / "velites"
    run.project = "measure-test"
    run.host_url = "http://host"
    run.worker_url = "http://worker"
    run.dsn = "dsn"
    run.skills_dir = run.work_dir / "skills"
    run.models_path = run.work_dir / "models.json"
    run.worker_config = run.work_dir / "worker.yaml"
    run.vault_key = "vault"
    run.secrets = []
    run.env = {"COMPOSE_PROJECT_NAME": run.project}
    run.storage = object()
    run.cgroup_reader_id = ""
    run.control_token = "token"
    events: list[tuple[str, ...]] = []
    evidence_ready: list[bool] = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        events.append(arguments)
        if arguments == ("config", "--format", "json"):
            return json.dumps({"name": run.project})
        if arguments == ("down", "--volumes", "--remove-orphans"):
            evidence_ready.append(
                (run.output_dir / "run-report.json").is_file()
                and all(
                    (run.output_dir / iteration / name).is_file()
                    for iteration in ("cold-1", "warm-1")
                    for name in ("result.json", "host.log")
                )
            )
        return "ready"

    def write_logs(_run: object, iteration_dir: Path) -> None:
        (iteration_dir / "host.log").write_text("sanitized\n", encoding="utf-8")

    run.compose = compose
    run._wait_http = lambda _url: None
    run._control_token = lambda: "token"
    pending_response = _pending_response()
    claim_previous: list[dict | None] = []

    def worker_request(method, path, _token, **kwargs):
        if method == "GET" and path == "/api/status":
            payload = _refused_status()
        elif method == "PUT" and kwargs.get("json", {}).get("disabled_runtimes"):
            payload = pending_response
        else:
            payload = {}
        return SimpleNamespace(json=lambda: payload)

    def enable_claim_mock(_token, _session, previous_status=None):
        claim_previous.append(previous_status)

    run._worker_request = worker_request
    run._enable_claim = enable_claim_mock
    run._submit = lambda *_args, **_kwargs: {"direct_upload_verified": True}
    monkeypatch.setattr(workload, "seed_measurement_workspaces", lambda *_args: None)
    monkeypatch.setattr(workload, "ensure_measurement_bucket", lambda *_args: None)
    monkeypatch.setattr(workload, "ensure_admin_session", lambda _url: object())
    monkeypatch.setattr(workload, "cgroup_reader", lambda *_args: object())
    monkeypatch.setattr(workload, "CgroupSampler", _FakeSampler)
    monkeypatch.setattr(workload, "write_iteration_metadata", lambda *_args: None)
    monkeypatch.setattr(workload, "write_sanitized_logs", write_logs)
    monkeypatch.setattr(
        workload, "measure_upload_backlog", lambda *_args: {"direct_upload_verified": True}
    )
    monkeypatch.setattr(workload, "require_pending_restart", lambda *_args: {"observed": True})
    monkeypatch.setattr(workload, "refresh_cgroup_reader", lambda *_args: None)
    monkeypatch.setattr(
        workload, "WorkspaceWorkerControl", lambda _dsn: SimpleNamespace(resume=lambda _ws: None)
    )
    monkeypatch.setattr(workload.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        workload.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="123", stderr="", returncode=0),
    )
    monkeypatch.setattr(
        workload.AgentRegisterTokenStore,
        "issue_register_token",
        lambda _self, **_kwargs: ("token-id", "plaintext"),
    )

    reports = [run.run_iteration("cold", 1), run.run_iteration("warm", 1)]
    preflight = (
        "exec",
        "-T",
        "worker",
        "/app/data/bin/velites",
        "sandbox",
        "wrap",
        "--cwd",
        "/tmp",
        "--",
        "/bin/true",
    )
    assert (
        events.index(("up", "-d", "worker"))
        < events.index(preflight)
        < events.index(("ps", "-q", "worker"))
    )
    assert claim_previous[1] is pending_response["status"]
    assert claim_previous[5] is pending_response["status"]
    outer = finalize_measurement_run(run, run.output_dir, reports, write_run_report)
    for index, iteration in enumerate(("cold-1", "warm-1")):
        result_doc = json.loads(
            (run.output_dir / iteration / "result.json").read_text(encoding="utf-8")
        )
        summary_doc = json.loads(
            (run.output_dir / iteration / "summary.json").read_text(encoding="utf-8")
        )
        raw = [
            json.loads(line)
            for line in (run.output_dir / iteration / "samples.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        expected = {
            "memory": sum(row["kind"] == "memory" for row in raw),
            "pids": sum(row["kind"] == "pids" for row in raw),
        }
        assert result_doc["success"] is True
        assert result_doc["sample_counts"] == expected
        assert summary_doc["groups"]["all/all"]["sample_counts"] == expected
        assert outer["iterations"][index]["sample_counts"] == expected
        assert summary_doc["groups"][f"{iteration}/idle-registered"]["sample_counts"] != expected
    assert [event for event in events if event[0] == "down"] == [
        ("down", "--remove-orphans", "-v"),
        ("down", "--remove-orphans"),
        ("down", "--remove-orphans"),
        ("down", "--remove-orphans"),
        ("down", "--volumes", "--remove-orphans"),
    ]
    assert evidence_ready == [True]


def _successful_run(project: str):
    run = SimpleNamespace(
        stub=None,
        secrets=[],
        project=project,
        env={"COMPOSE_PROJECT_NAME": project},
        events=[],
    )
    run.prepare = lambda: None
    run.run_iteration = lambda mode, repetition: {
        "iteration": f"{mode}-{repetition}",
        "success": True,
        "scenarios": {},
    }

    def compose(*arguments: str, **_kwargs: object) -> str:
        run.events.append(arguments)
        if arguments == ("config", "--format", "json"):
            return json.dumps({"name": run.project})
        return ""

    run.compose = compose
    return run


def test_nonempty_output_dir_has_no_side_effects_and_preserves_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    output = tmp_path / "evidence"
    (output / "cold-1").mkdir(parents=True)
    samples = output / "cold-1" / "samples.jsonl"
    samples.write_text("old samples\n", encoding="utf-8")
    report = output / "run-report.json"
    report.write_text('{"old": true}\n', encoding="utf-8")
    run = _successful_run("measure-must-not-start")
    monkeypatch.setattr(workload, "MeasurementRun", lambda _args: run)
    with pytest.raises(ContractViolationError, match="output-dir"):
        workload.main(["--output-dir", str(output)])
    assert run.events == []
    assert samples.read_text(encoding="utf-8") == "old samples\n"
    assert report.read_text(encoding="utf-8") == '{"old": true}\n'
    file_output = tmp_path / "not-a-directory"
    file_output.write_text("old file", encoding="utf-8")
    with pytest.raises(ContractViolationError, match="output-dir"):
        workload.main(["--output-dir", str(file_output)])
    assert file_output.read_text(encoding="utf-8") == "old file"
    assert run.events == []
    assert not (output.parent / f".{output.name}.worker-resource-measurement.lock").exists()


def test_main_allows_absent_and_empty_output_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    absent = tmp_path / "new-results"
    empty = tmp_path / "empty-results"
    empty.mkdir()
    for index, output in enumerate((absent, empty)):
        run = _successful_run(f"measure-new-{index}")
        monkeypatch.setattr(workload, "MeasurementRun", lambda _args, run=run: run)
        assert (
            workload.main(
                [
                    "--output-dir",
                    str(output),
                    "--cold-repetitions",
                    "1",
                    "--warm-repetitions",
                    "0",
                ]
            )
            == 0
        )
        report = json.loads((output / "run-report.json").read_text(encoding="utf-8"))
        assert report["success"] is True
        assert report["iterations"][0]["iteration"] == "cold-1"


@pytest.mark.parametrize("alias", [False, True])
def test_main_rejects_concurrent_output_dir_owner_without_mixing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: bool
) -> None:
    from scripts.stress import worker_resource_workload as workload

    output = tmp_path / "shared-results"
    contender_output = tmp_path / "alias-results" if alias else output
    if alias:
        contender_output.symlink_to(output, target_is_directory=True)
    prepare_started = threading.Event()
    finish_prepare = threading.Event()
    first = _successful_run("measure-first")
    second = _successful_run("measure-second")
    constructed = 0

    def prepare() -> None:
        output.mkdir(parents=True, exist_ok=True)
        prepare_started.set()
        assert finish_prepare.wait(timeout=5)

    first.prepare = prepare
    first.run_iteration = lambda mode, repetition: {
        "iteration": f"{mode}-{repetition}",
        "success": True,
        "owner": "first",
        "scenarios": {},
    }

    def factory(_args):
        nonlocal constructed
        constructed += 1
        return first if constructed == 1 else second

    monkeypatch.setattr(workload, "MeasurementRun", factory)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(workload.main, ["--output-dir", str(output)])
    try:
        assert prepare_started.wait(timeout=5)
        with pytest.raises(ContractViolationError, match="already being measured"):
            workload.main(["--output-dir", str(contender_output)])
        assert constructed == 1
        assert not (output / "run-report.json").exists()
    finally:
        finish_prepare.set()
        assert future.result(timeout=5) == 0
        executor.shutdown()
    report = json.loads((output / "run-report.json").read_text(encoding="utf-8"))
    assert report["iterations"][0]["owner"] == "first"
    assert not (output / f".{output.name}.worker-resource-measurement.lock").exists()
    with pytest.raises(ContractViolationError, match="absent or empty"):
        workload.main(["--output-dir", str(contender_output)])
    assert constructed == 1


def test_main_rechecks_output_dir_after_lock_before_constructing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    output = tmp_path / "transition-results"
    first = _successful_run("measure-first")
    second = _successful_run("measure-second")
    constructed = 0
    checks = 0
    first_report = b""

    def factory(_args):
        nonlocal constructed
        constructed += 1
        return first if constructed == 1 else second

    original_check = workload.require_empty_output_dir

    def checked(path: Path) -> None:
        nonlocal checks, first_report
        checks += 1
        original_check(path)
        if checks == 1:
            assert workload.main(["--output-dir", str(output)]) == 0
            first_report = (output / "run-report.json").read_bytes()

    monkeypatch.setattr(workload, "MeasurementRun", factory)
    monkeypatch.setattr(workload, "require_empty_output_dir", checked)
    with pytest.raises(ContractViolationError, match="absent or empty"):
        workload.main(["--output-dir", str(output)])
    assert constructed == 1
    assert second.events == []
    assert (output / "run-report.json").read_bytes() == first_report
    lock_path = output.parent / f".{output.name}.worker-resource-measurement.lock"
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    (output / "run-report.json").unlink()
    succeeding = _successful_run("measure-after-recheck")
    monkeypatch.setattr(workload, "MeasurementRun", lambda _args: succeeding)
    assert workload.main(["--output-dir", str(output)]) == 0
    assert json.loads((output / "run-report.json").read_text())["success"] is True


def test_main_releases_output_dir_lock_after_prepare_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    output = tmp_path / "failure-results"
    failing = _successful_run("measure-failing")

    def prepare() -> None:
        output.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("injected prepare failure")

    failing.prepare = prepare
    monkeypatch.setattr(workload, "MeasurementRun", lambda _args: failing)
    assert workload.main(["--output-dir", str(output)]) == 1
    (output / "run-report.json").unlink()
    succeeding = _successful_run("measure-after-failure")
    monkeypatch.setattr(workload, "MeasurementRun", lambda _args: succeeding)
    assert workload.main(["--output-dir", str(output)]) == 0
    assert json.loads((output / "run-report.json").read_text())["success"] is True


def test_main_cleans_project_volumes_after_prepare_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    events: list[str] = []
    report_ready: list[bool] = []
    run = SimpleNamespace(
        stub=None,
        secrets=[],
        project="measure-failure",
        env={"COMPOSE_PROJECT_NAME": "measure-failure"},
    )

    def prepare() -> None:
        events.append("prepare")
        raise RuntimeError("injected prepare failure")

    def compose(*arguments: str, **_kwargs: object) -> str:
        events.append(" ".join(arguments))
        if arguments == ("config", "--format", "json"):
            return json.dumps({"name": run.project})
        if arguments == ("down", "--volumes", "--remove-orphans"):
            report_ready.append((tmp_path / "run-report.json").is_file())
        return ""

    run.prepare = prepare
    run.compose = compose
    monkeypatch.setattr(workload, "MeasurementRun", lambda _args: run)
    assert workload.main(["--output-dir", str(tmp_path)]) == 1
    assert events == [
        "prepare",
        "config --format json",
        "down --volumes --remove-orphans",
    ]
    assert report_ready == [True]
    report = json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8"))
    assert report["success"] is False
    assert "injected prepare failure" in report["iterations"][0]["error"]


def test_final_cleanup_rejects_changed_project_identity(tmp_path: Path) -> None:
    events: list[tuple[str, ...]] = []
    run = SimpleNamespace(
        project="measure-expected",
        env={"COMPOSE_PROJECT_NAME": "measure-expected"},
        secrets=[],
    )

    def compose(*arguments: str, **_kwargs: object) -> str:
        events.append(arguments)
        return json.dumps({"name": "measure-other"})

    run.compose = compose
    report = finalize_measurement_run(
        run, tmp_path, [{"iteration": "cold-1", "success": True}], write_run_report
    )
    assert report["success"] is False
    assert "identity mismatch" in report["cleanup_error"]
    assert events == [("config", "--format", "json")]
    persisted = json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8"))
    assert persisted["cleanup_error"] == report["cleanup_error"]


def test_measurement_host_identity_matches_mount_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_workload as workload

    monkeypatch.setattr(workload.os, "getuid", lambda: 1234)
    monkeypatch.setattr(workload.os, "getgid", lambda: 2345)
    args = workload.parse_args(
        [
            "--work-dir",
            str(tmp_path / "work"),
            "--output-dir",
            str(tmp_path / "results"),
            "--velites-bin",
            str(tmp_path / "velites"),
        ]
    )
    monkeypatch.setattr(workload.platform, "system", lambda: "Linux")
    native = workload.MeasurementRun(args)
    assert native.env["MEASUREMENT_HOST_UID"] == "1234"
    assert native.env["MEASUREMENT_HOST_GID"] == "2345"
    monkeypatch.setattr(workload.platform, "system", lambda: "Darwin")
    desktop = workload.MeasurementRun(args)
    assert desktop.env["MEASUREMENT_HOST_UID"] == "0"
    assert desktop.env["MEASUREMENT_HOST_GID"] == "0"


_WORKFLOW_INPUTS = {
    "MEASUREMENT_COLD_REPETITIONS": "cold_repetitions",
    "MEASUREMENT_WARM_REPETITIONS": "warm_repetitions",
    "MEASUREMENT_PIDS_INTERVAL_MS": "pids_interval_ms",
    "MEASUREMENT_MEMORY_INTERVAL_MS": "memory_interval_ms",
}


def _render_workflow_inputs(document: dict, inputs: dict[str, str]) -> list[str]:
    arguments: list[str] = []
    for env_name, input_name in _WORKFLOW_INPUTS.items():
        expression = document["jobs"]["measure"]["env"][env_name]
        assert expression.startswith("${{ inputs.")
        body = expression[3:-2].strip().removeprefix("inputs.")
        key, _, fallback = body.partition("||")
        value = inputs.get(key.strip(), "")
        if not value and fallback:
            value = fallback.strip().strip("'")
        arguments.extend((f"--{input_name.replace('_', '-')}", value))
    return arguments


def test_workflow_dispatch_and_pr_inputs_reach_real_parser() -> None:
    workflow_path = Path(__file__).parents[2] / ".github/workflows/worker-resource-measurement.yml"
    source = workflow_path.read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    triggers = document[True]
    assert set(triggers) == {"workflow_dispatch", "pull_request"}
    assert triggers["pull_request"]["paths"] == [
        ".github/workflows/worker-resource-measurement.yml",
        "deploy/.env.example",
        "deploy/compose.host.yaml",
        "deploy/compose.worker.measurement.yaml",
        "deploy/compose.worker.standalone.yaml",
        "deploy/compose.worker.yaml",
        "Dockerfile",
        "scripts/install-worker.sh",
        "scripts/stress/worker_resource_*.py",
        "tests/scripts/test_install_worker*.py",
        "tests/stress/test_worker_resource_*.py",
    ]
    definitions = triggers["workflow_dispatch"]["inputs"]
    defaults = {name: definition["default"] for name, definition in definitions.items()}
    for event_inputs in (
        defaults,
        {name: "" for name in definitions},
    ):
        args = parse_args(_render_workflow_inputs(document, event_inputs))
        assert args.cold_repetitions == 1
        assert args.warm_repetitions == 2
        assert args.pids_interval_ms == 100
        assert args.memory_interval_ms == 500
    custom = parse_args(
        _render_workflow_inputs(
            document,
            {
                "cold_repetitions": "3",
                "warm_repetitions": "4",
                "pids_interval_ms": "125",
                "memory_interval_ms": "750",
            },
        )
    )
    assert custom.cold_repetitions == 3
    assert custom.warm_repetitions == 4
    assert custom.pids_interval_ms == 125
    assert custom.memory_interval_ms == 750
    assert document["jobs"]["measure"]["env"]["MEASUREMENT_NET_ADMIN"] == "NET_ADMIN"
    assert document["jobs"]["measure"]["env"]["MEASUREMENT_APPARMOR"] == "apparmor:unconfined"
    assert (
        document["jobs"]["measure"]["env"]["MEASUREMENT_MEM_LIMIT"]
        == "${{ inputs.mem_limit || '0' }}"
    )
    assert (
        document["jobs"]["measure"]["env"]["MEASUREMENT_PIDS_LIMIT"]
        == "${{ inputs.pids_limit || '-1' }}"
    )
    fault = yaml.safe_dump(document["jobs"]["fault-injection"])
    assert "OOMKilled" in fault and "can't start new thread" in fault
    lifecycle = "\n".join(
        step.get("run", "") for step in document["jobs"]["installer-lifecycle"]["steps"]
    )
    assert "uv run pytest -q tests/scripts/test_install_worker.py" in lifecycle
    assert lifecycle.count("config --format json") == 3
    default_render = next(
        step["run"]
        for step in document["jobs"]["installer-lifecycle"]["steps"]
        if step.get("name") == "Assert default resource limits in real Compose renders"
    )
    for key in (
        "AGENT_WORKER_RESOURCE_LIMITS_MODE",
        "AGENT_WORKER_MEM_LIMIT",
        "AGENT_WORKER_PIDS_LIMIT",
    ):
        assert f"-u {key}" in default_render
    assert "--env-file /dev/null" in default_render
    for injected in (
        "AGENT_WORKER_RESOURCE_LIMITS_MODE=default",
        "AGENT_WORKER_MEM_LIMIT=256m",
        "AGENT_WORKER_PIDS_LIMIT=96",
    ):
        assert injected not in default_render
    for name in ("compose.worker.yaml", "compose.worker.standalone.yaml", "compose.host.yaml"):
        assert name in lifecycle
    assert document["permissions"] == {"contents": "read"}
    matrix = document["jobs"]["measure"]["strategy"]["matrix"]["include"]
    assert len(matrix) == 4
    default = next(item for item in matrix if item["envelope"] == "default")
    assert (
        default["agent_concurrency"],
        default["code_concurrency"],
        default["upload_concurrency"],
    ) == (10, 0, 4)
    assert "ubuntu-24.04" in source
    assert "worker-resource-results/" in source
    assert "data/worker-resource-measurement/work" not in source
    assert "secrets." not in source
    assert "workflow_run" not in source


def test_metadata_distinguishes_measurement_apparmor_from_shipped_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_evidence as evidence

    monkeypatch.setattr(evidence, "collect_host_metadata", lambda *_args: {})
    models = tmp_path / "models.json"
    models.write_text("{}", encoding="utf-8")
    run = SimpleNamespace(
        env={"MEASUREMENT_APPARMOR": "apparmor:unconfined"},
        velites=tmp_path / "velites",
        models_path=models,
        args=SimpleNamespace(
            agent_concurrency=1,
            code_concurrency=1,
            upload_concurrency=1,
            jobs_per_wave=2,
            artifact_chars=1,
            pids_interval_ms=100,
            memory_interval_ms=500,
        ),
    )
    evidence.write_iteration_metadata(run, tmp_path, "cold-1", "container")
    measurement = json.loads((tmp_path / "metadata.json").read_text())["measurement"]
    assert measurement["shipped_apparmor"] == "docker-default"
    assert measurement["functional_apparmor_override"] == "apparmor:unconfined"
