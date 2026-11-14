"""Failure and argument-boundary contracts for direct smoke collection."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_db


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_smoke(tmp_path: Path, uv_stub: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", scripts)
    for helper in ("gate-jobs.sh", "gate-queue.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / helper, scripts)
    _write_executable(fake_bin / "uv", uv_stub)
    gate_log = tmp_path / "gate.log"
    env = os.environ.copy()
    env.update(
        {
            "AGENT_LEGION_TEST_WORKERS": "2",
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "smoke",
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        [str(scripts / "check-quick-backend.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, gate_log


def test_selector_failure_stops_before_pytest(tmp_path: Path) -> None:
    result, gate_log = _run_smoke(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'if [[ " $* " == *" scripts.pytest_smoke_selection "* ]]; then\n'
        "  exit 9\n"
        "fi\n",
    )

    assert result.returncode == 9, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "scripts.pytest_smoke_selection" in calls
    assert "pytest -q" not in calls
    assert "Smoke collection:" not in result.stdout


def test_smoke_filename_with_spaces_remains_one_pytest_argument(tmp_path: Path) -> None:
    result, gate_log = _run_smoke(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ " $* " == *" scripts.pytest_smoke_selection "* ]]; then\n'
        '  printf "tests/test fake.py\\n"\n'
        "  exit 0\n"
        "fi\n"
        'printf "<%s>\\n" "$@" >>"$GATE_LOG"\n',
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "<tests/test fake.py>" in calls
    assert "<tests/test>" not in calls
