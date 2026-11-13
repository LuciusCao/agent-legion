"""Contract tests for quick-gate Vitest worker-budget injection."""

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
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_gate(
    tmp_path: Path,
    *,
    workers: str | None = None,
    ci: str | None = None,
    rust: bool = False,
    rust_workers: str | None = None,
) -> list[str]:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", scripts)
    for helper in ("lane-paths.sh", "gate-jobs.sh", "gate-queue.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / helper, scripts)
    _write_executable(scripts / "check-quick-backend.sh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "${AGENT_LEGION_FRONTEND_TEST_WORKERS:-unset}" >>"$GATE_LOG"\n',
    )
    _write_executable(fake_bin / "sysctl", "#!/usr/bin/env bash\nprintf '10\\n'\n")
    if rust:
        (tmp_path / "velites").mkdir()
        _write_executable(
            fake_bin / "cargo", '#!/usr/bin/env bash\nprintf "cargo:%s\\n" "$*" >>"$GATE_LOG"\n'
        )
    env = os.environ.copy()
    env.pop("AGENT_LEGION_FRONTEND_TEST_WORKERS", None)
    env.pop("AGENT_LEGION_RUST_WORKERS", None)
    env.pop("CI", None)
    if workers is not None:
        env["AGENT_LEGION_FRONTEND_TEST_WORKERS"] = workers
    if rust_workers is not None:
        env["AGENT_LEGION_RUST_WORKERS"] = rust_workers
    if ci is not None:
        env["CI"] = ci
    env.update(
        {
            "GATE_LANES": "frontend rust" if rust else "frontend",
            "GATE_LOG": str(tmp_path / "workers.log"),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        [str(scripts / "check-quick.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "workers.log").read_text(encoding="utf-8").splitlines()


def test_quick_gate_uses_shared_machine_budget_for_vitest(tmp_path: Path) -> None:
    assert _run_gate(tmp_path) == ["8", "8"]


def test_explicit_vitest_workers_override_machine_budget(tmp_path: Path) -> None:
    assert _run_gate(tmp_path, workers="6") == ["6", "6"]


def test_ci_keeps_frontend_script_default_workers(tmp_path: Path) -> None:
    assert _run_gate(tmp_path, ci="true") == ["unset", "unset"]


def test_test_rest_shares_budget_between_frontend_and_rust(tmp_path: Path) -> None:
    calls = _run_gate(tmp_path, rust=True)

    assert calls.count("8") == 2
    assert [call for call in calls if call.startswith("cargo:test")] == ["cargo:test --locked -j 2"]


def test_explicit_rust_workers_override_shared_budget(tmp_path: Path) -> None:
    calls = _run_gate(tmp_path, rust=True, rust_workers="5")

    assert [call for call in calls if call.startswith("cargo:test")] == ["cargo:test --locked -j 5"]
