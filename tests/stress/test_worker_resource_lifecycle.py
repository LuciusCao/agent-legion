from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.stress import worker_resource_restart as restart
from scripts.stress.worker_resource_measurement import summarize_samples
from scripts.stress.worker_resource_restart import (
    ExecutionReadinessError,
    enable_claim,
    require_current_registration,
)
from scripts.stress.worker_resource_workload import MeasurementRun, write_run_report

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
                "iteration": "cold-1",
                "scenario": "agent",
                "values": {"current_bytes": 1, "peak_bytes": 1, "max": "max", "stat": {}},
            },
            {
                "kind": "memory",
                "iteration": "cold-1",
                "scenario": "agent",
                "values": {"current_bytes": 2, "peak_bytes": 2, "max": "max", "stat": {}},
            },
            {
                "kind": "pids",
                "iteration": "cold-1",
                "scenario": "agent",
                "values": {"current": 1, "max": "max", "events": {"max": 0}},
            },
            {
                "kind": "pids",
                "iteration": "cold-1",
                "scenario": "agent",
                "values": {"current": 2, "max": "max", "events": {"max": 0}},
            },
            {
                "kind": "pids",
                "iteration": "cold-1",
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
    run.env = {}
    run.storage = object()
    run.cgroup_reader_id = ""
    run.control_token = "token"
    run.compose = lambda *_args, **_kwargs: "ready"
    run._wait_http = lambda _url: None
    run._control_token = lambda: "token"
    run._worker_request = lambda *_args, **_kwargs: SimpleNamespace(json=lambda: {"config": {}})
    run._enable_claim = lambda *_args, **_kwargs: None
    run._submit = lambda *_args, **_kwargs: {"direct_upload_verified": True}
    monkeypatch.setattr(workload, "seed_measurement_workspaces", lambda *_args: None)
    monkeypatch.setattr(workload, "ensure_measurement_bucket", lambda *_args: None)
    monkeypatch.setattr(workload, "ensure_admin_session", lambda _url: object())
    monkeypatch.setattr(workload, "cgroup_reader", lambda *_args: object())
    monkeypatch.setattr(workload, "CgroupSampler", _FakeSampler)
    monkeypatch.setattr(workload, "write_iteration_metadata", lambda *_args: None)
    monkeypatch.setattr(workload, "write_sanitized_logs", lambda *_args: None)
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

    result = run.run_iteration("cold", 1)
    outer = write_run_report(run.output_dir, [result])
    result_path = run.output_dir / "cold-1" / "result.json"
    summary_path = run.output_dir / "cold-1" / "summary.json"
    raw_path = run.output_dir / "cold-1" / "samples.jsonl"
    result_doc = json.loads(result_path.read_text(encoding="utf-8"))
    summary_doc = json.loads(summary_path.read_text(encoding="utf-8"))
    raw = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    expected = {
        "memory": sum(row["kind"] == "memory" for row in raw),
        "pids": sum(row["kind"] == "pids" for row in raw),
    }
    assert result_doc["success"] is True
    assert result_doc["sample_counts"] == expected
    assert summary_doc["groups"]["all/all"]["sample_counts"] == expected
    assert outer["iterations"][0]["sample_counts"] == expected
    assert summary_doc["groups"]["cold-1/idle-registered"]["sample_counts"] != expected
