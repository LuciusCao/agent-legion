from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(path: Path, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    for key in (
        "BACKEND_GATE_PHASE",
        "FRONTEND_API_CHECK",
        "FRONTEND_GATE_PHASE",
        "FRONTEND_TEST_MODE",
        "GATE_LANES",
        "GATE_TIER",
    ):
        process_env.pop(key, None)
    process_env.update(env)
    return subprocess.run(
        [str(path)],
        cwd=cwd,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_quick_gate_starts_backend_and_frontend_lanes_concurrently(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    marker = tmp_path / "frontend.started"

    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        # 20s margin (was 5s): under a loaded gate the stub frontend lane can
        # take several seconds to be scheduled; the concurrency assertion only
        # needs "backend did not exit before frontend started", not a tight race.
        "for attempt in $(seq 1 400); do\n"
        '  [[ -f "$GATE_MARKER" ]] && exit 0\n'
        "  sleep 0.05\n"
        "done\n"
        "exit 9\n",
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        '#!/usr/bin/env bash\ntouch "$GATE_MARKER"\n',
    )

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_MARKER": str(marker)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Parallel quick gate passed" in result.stdout


def test_quick_gate_reports_each_lane_status(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _write_executable(scripts / "check-quick-backend.sh", "#!/usr/bin/env bash\nexit 7\n")
    _write_executable(scripts / "check-quick-frontend.sh", "#!/usr/bin/env bash\nexit 0\n")

    result = _run(quick_gate, cwd=tmp_path, env={})

    assert result.returncode == 1
    assert "backend=7 frontend=0" in result.stderr


def test_quick_gate_hoists_api_contract_out_of_parallel_static_round(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    gate_log = tmp_path / "gate.log"

    _write_executable(scripts / "check-quick-backend.sh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        "printf '%s:%s\\n' \"${FRONTEND_GATE_PHASE:-unset}\" "
        '"${FRONTEND_API_CHECK:-unset}" >>"$GATE_LOG"\n',
    )

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_LOG": str(gate_log)})

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8").splitlines()
    # The static lane skips the inline api:check; the contract runs exactly
    # once as the integration step between the static and test rounds.
    assert calls == ["static:0", "api-contract:unset", "test:1"]


def test_full_gate_reuses_coverage_tests_and_bundle_only_build(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    frontend = tmp_path / "frontend"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    frontend.mkdir()
    fake_bin.mkdir()
    full_gate = scripts / "check.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check.sh", full_gate)
    gate_log = tmp_path / "gate.log"

    _write_executable(
        scripts / "check-quick.sh",
        '#!/usr/bin/env bash\nprintf "quick:%s\\n" "${FRONTEND_TEST_MODE:-unset}" >>"$GATE_LOG"\n',
    )
    _write_executable(scripts / "check-deps-audit.sh", "#!/usr/bin/env bash\nexit 0\n")
    for command in ("uv", "npm"):
        _write_executable(
            fake_bin / command,
            f'#!/usr/bin/env bash\nprintf "{command}:%s\\n" "$*" >>"$GATE_LOG"\n',
        )

    result = _run(
        full_gate,
        cwd=tmp_path,
        env={"GATE_LOG": str(gate_log), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("quick:coverage") == 1
    assert calls.count("npm:run build:bundle") == 1
    assert not any("test:coverage" in call for call in calls)
    assert any("pytest -q tests/full" in call for call in calls)
    assert any("coverage report" in call for call in calls)


def test_backend_gate_emits_junit_durations_and_rerun_report(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    results = tmp_path / "results"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'printf "rerun:%s\\n" "${AGENT_LEGION_RERUN_REPORT:-unset}" >>"$GATE_LOG"\n',
    )

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "AGENT_LEGION_TEST_RESULTS_DIR": str(results),
            "AGENT_LEGION_TEST_RESULT_NAME": "quick",
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "smoke",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PostgreSQL offline" in result.stdout
    calls = gate_log.read_text(encoding="utf-8")
    assert "not postgres and not repository_gate" in calls
    assert "smoke and not repository_gate" not in calls
    assert "--durations=30" in calls
    assert f"--junitxml={results / 'quick-junit.xml'}" in calls
    assert "-p scripts.pytest_telemetry" in calls
    assert f"rerun:{results / 'quick-reruns.json'}" in calls


def test_frontend_gate_emits_junit_and_json_reports(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    frontend = tmp_path / "frontend"
    fake_bin = tmp_path / "bin"
    results = tmp_path / "results"
    scripts.mkdir()
    frontend.mkdir()
    fake_bin.mkdir()
    frontend_gate = scripts / "check-quick-frontend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-frontend.sh", frontend_gate)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "npm",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$GATE_LOG"\n',
    )

    result = _run(
        frontend_gate,
        cwd=tmp_path,
        env={
            "AGENT_LEGION_TEST_RESULTS_DIR": str(results),
            "FRONTEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "run test -- --reporter=default --reporter=junit" in calls
    assert f"--outputFile.junit={results / 'vitest-junit.xml'}" in calls
    assert "--reporter=json" in calls
    assert f"--outputFile.json={results / 'vitest-results.json'}" in calls
