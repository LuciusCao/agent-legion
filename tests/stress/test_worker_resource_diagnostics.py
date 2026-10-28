from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.stress.worker_resource_workload import MeasurementRun

pytestmark = pytest.mark.no_db


def _code_row(absolute: bool = False, iteration: str = "cold-1") -> dict:
    job = f"resource~ucode_resource~ucode_measurement-ref:measurement-{iteration}-code-0"
    relative = f"logs/jobs/by-job/ab/{job}/resource_code.log"
    return {
        "id": 1,
        "job_id": job,
        "node_key": "resource_code",
        "status": "failed",
        "error_message": "no result",
        "command_json": ["--password", "unknown-secret"],
        "log_path": f"/var/lib/agent-legion/{relative}" if absolute else relative,
        "run_dir": f"jobs/resource_code/{job}/runs/resource_code/token",
    }


def _diagnostic_run(log: str = "sandbox stderr secret") -> object:
    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        return "service log" if arguments[:2] == ("logs", "--no-color") else log

    return SimpleNamespace(
        args=SimpleNamespace(code_concurrency=0),
        dsn="dsn",
        secrets=["secret"],
        control_token="",
        cgroup_reader_id="",
        env={},
        compose=compose,
        calls=calls,
    )


def _diagnostic_connection(rows: list[dict]):
    from contextlib import contextmanager

    @contextmanager
    def connection(_dsn: str):
        current = [row for row in rows if "measurement-cold-1" in row["job_id"]]
        yield SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchall=lambda: current))

    return connection


def test_production_relative_absolute_and_iteration_scoped_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress.worker_resource_reporting import write_sanitized_logs

    rows = [_code_row(), _code_row(absolute=True), _code_row(iteration="cold-0")]
    monkeypatch.setattr(
        "scripts.stress.worker_resource_diagnostics.read_connection", _diagnostic_connection(rows)
    )
    run = _diagnostic_run()
    write_sanitized_logs(run, tmp_path)
    assert "sandbox stderr" in (tmp_path / "host-code.log").read_text()
    metadata = (tmp_path / "host-code-runs.json").read_text()
    assert "unknown-secret" not in metadata and "cold-0" not in metadata
    assert "O_NOFOLLOW" in str(run.calls) and "O_DIRECTORY" in str(run.calls)
    run.args.code_concurrency = 1
    other = tmp_path / "worker-code"
    other.mkdir()
    write_sanitized_logs(run, other)
    assert not (other / "host-code.log").exists()


def test_log_path_rejects_parent_other_job_and_run_dir_mismatch() -> None:
    from scripts.stress.worker_resource_diagnostics import _log_path

    row = _code_row()
    for path in ("../escape.log", "logs/jobs/by-job/ab/other/resource_code.log", "/etc/passwd"):
        with pytest.raises(ValueError):
            _log_path({**row, "log_path": path})
    with pytest.raises(ValueError, match="run_dir ownership"):
        _log_path({**row, "run_dir": "jobs/other/runs/resource_code/token"})


def test_read_log_rejects_intermediate_and_leaf_symlinks(tmp_path: Path) -> None:
    import subprocess

    from scripts.stress.worker_resource_diagnostics import _READ_LOG

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "resource_code.log").write_text("outside", encoding="utf-8")
    root = tmp_path / "root"
    job = "resource~ucode_resource~ucode_measurement-ref:measurement-cold-1-code-0"
    relative = f"logs/jobs/by-job/ab/{job}/resource_code.log"
    for leaf in (False, True):
        link = (
            root / f"logs/jobs/by-job/ab/{job}/resource_code.log"
            if leaf
            else root / "logs/jobs/by-job/ab"
        )
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(
            outside / "resource_code.log" if leaf else outside, target_is_directory=not leaf
        )
        result = subprocess.run(
            ["python3", "-c", _READ_LOG, relative, str(root)], capture_output=True
        )
        assert result.returncode != 0
        link.unlink()
    (root / f"logs/jobs/by-job/ab/{job}/resource_code.log").write_text("safe")
    result = subprocess.run(["python3", "-c", _READ_LOG, relative, str(root)], capture_output=True)
    assert result.returncode == 0 and result.stdout == b"safe"


def test_operational_error_preserves_original_failure_cleanup_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    run = MeasurementRun.__new__(MeasurementRun)
    run.args = SimpleNamespace(code_concurrency=0)
    run.output_dir = tmp_path / "cold-1"
    run.secrets = []
    run.control_token = ""
    run.cgroup_reader_id = ""
    downs = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        if arguments[0] == "up":
            raise RuntimeError("original workload failure")
        downs.append(arguments) if arguments[0] == "down" else None
        return ""

    run.compose = compose
    monkeypatch.setattr(
        "scripts.stress.worker_resource_workload.write_sanitized_logs",
        lambda *_args: (_ for _ in ()).throw(psycopg.OperationalError("db down")),
    )
    result = run.run_iteration("cold", 1)
    assert result["error"] == "RuntimeError: original workload failure"
    assert "OperationalError" in result["diagnostic_error"]
    assert len(downs) == 2 and (tmp_path / "cold-1/cold-1/result.json").is_file()


def test_artifact_byte_budgets_and_unknown_argv_never_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress.worker_resource_reporting import write_sanitized_logs

    rows = [{**_code_row(), "id": index, "error_message": "界" * 100000} for index in range(20)]
    monkeypatch.setattr(
        "scripts.stress.worker_resource_diagnostics.read_connection", _diagnostic_connection(rows)
    )
    run = _diagnostic_run(log="x" * 65536)
    write_sanitized_logs(run, tmp_path)
    assert (tmp_path / "host-code.log").stat().st_size <= 1 << 20
    metadata_path = tmp_path / "host-code-runs.json"
    assert metadata_path.stat().st_size <= 256 * 1024 + 2
    assert "unknown-secret" not in metadata_path.read_text()
