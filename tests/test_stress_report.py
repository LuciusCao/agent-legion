from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("scripts.stress.run_e2e_stress")

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "stress"))

from _e2e_readiness import wait_for_server, wait_for_snapshot_readiness
from run_e2e_stress import (
    E2EStressReport,
    _find_free_port,
    _iso_now,
    _make_run_dir,
    _parse_args,
    run,
)


def test_e2e_stress_report_to_dict_round_trip():
    report = E2EStressReport(
        run_id="run-1",
        started_at="2026-07-07T10:00:00Z",
        finished_at="2026-07-07T10:01:00Z",
        backend_command="python -m uvicorn server.app.main:app",
        frontend_command="playwright test",
        backend_metrics_path="stress-results/run-1/backend-metrics.json",
        frontend_metrics_path="stress-results/run-1/frontend-metrics.json",
        errors=[],
    )

    data = report.to_dict()

    assert data["run_id"] == "run-1"
    assert data["backend_command"].startswith("python")
    assert data["errors"] == []


def test_parse_args_uses_defaults():
    args = _parse_args([])

    assert args.agents == 100
    assert args.jobs == 10000
    assert args.duration == 900
    assert args.browser == "chromium"


def test_find_free_port_returns_available_port():
    port = _find_free_port()

    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_iso_now_returns_string():
    now = _iso_now()

    assert isinstance(now, str)
    assert "T" in now
    assert now.endswith("Z")


def test_make_run_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("run_e2e_stress.STRESS_RESULTS", tmp_path)

    run_dir = _make_run_dir()

    assert run_dir.exists()
    assert run_dir.parent == tmp_path


def test_wait_for_server_returns_false_when_no_server():
    # Use a port that is very unlikely to be listening.
    result = wait_for_server("http://127.0.0.1:1", timeout=0.1)

    assert result is False


def test_wait_for_snapshot_readiness_uses_stats_total_not_page_length():
    # The first page may contain very few jobs, but the stats aggregate must
    # drive readiness so large workspaces wait for the full seed count.
    def fake_request_json(url: str, timeout: float):
        return {
            "jobs": [{"id": "job-1"}],
            "stats": {"pending": 2500, "running": 2500},
        }

    with mock.patch("_e2e_readiness._request_json", side_effect=fake_request_json):
        result = wait_for_snapshot_readiness(
            "http://127.0.0.1:8000",
            "ws-stress",
            min_jobs=5000,
            timeout=1.0,
        )

    assert result is True


def test_run_writes_report_when_backend_start_fails(tmp_path, monkeypatch):
    import run_e2e_stress as runner

    monkeypatch.setattr(runner, "STRESS_RESULTS", tmp_path)

    def _start_backend(cmd: list[str], run_dir: Path):
        raise RuntimeError("backend start boom")

    monkeypatch.setattr(runner, "_start_backend", _start_backend)

    result = run(
        agents=1,
        jobs=1,
        duration=1,
        event_rate=1,
        browser="chromium",
        workspace="ws-stress",
        keep_server=False,
        skip_frontend=True,
    )

    assert result == 1
    report_files = list(tmp_path.rglob("report.md"))
    assert len(report_files) == 1
    content = report_files[0].read_text()
    assert "backend start boom" in content
    assert "## Backend Command" in content
