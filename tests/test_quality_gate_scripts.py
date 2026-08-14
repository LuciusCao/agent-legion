from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(path: Path, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    for key in (
        # AGENT_LEGION_TEST_DATABASE_URL: the unit tier pins an unreachable
        # offline URL for its whole pytest process; without scrubbing, a
        # simulated GATE_TIER=smoke run inherits it and the curated tier's
        # contract (never offline-pinned) cannot be verified on CI.
        "AGENT_LEGION_TEST_DATABASE_URL",
        "BACKEND_GATE_PHASE",
        "FRONTEND_API_CHECK",
        "FRONTEND_COVERAGE_BLOB_DIR",
        "FRONTEND_GATE_PHASE",
        "FRONTEND_TEST_MODE",
        "FRONTEND_TEST_PROJECT",
        "GATE_LANES",
        "GATE_SHARD",
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


def _quick_gate_fixture(scripts: Path) -> Path:
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _write_executable(scripts / "check-quick-backend.sh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(scripts / "check-quick-frontend.sh", "#!/usr/bin/env bash\nexit 0\n")
    return quick_gate


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_quick_gate_fast_lanes_are_not_padded_by_heartbeat_sleep(tmp_path: Path) -> None:
    """A lane that finishes in seconds must end the round within ~1s: the
    heartbeat loop polls in 1s steps instead of sleeping the whole interval."""
    quick_gate = _quick_gate_fixture(tmp_path / "scripts")

    started = time.monotonic()
    result = _run(quick_gate, cwd=tmp_path, env={"GATE_HEARTBEAT_SECONDS": "20"})
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stdout + result.stderr
    # The old sleep-then-check loop would pad each of the two rounds by the
    # full 20s heartbeat interval; the polling loop stays well under one.
    assert elapsed < 20
    assert "[gate:" not in result.stdout


def test_quick_gate_heartbeat_prints_running_lane_progress(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _write_executable(
        scripts / "check-quick-backend.sh",
        '#!/usr/bin/env bash\necho "backend lane working"\nsleep 6\n',
    )
    _write_executable(scripts / "check-quick-frontend.sh", "#!/usr/bin/env bash\nexit 0\n")

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_HEARTBEAT_SECONDS": "2"})

    assert result.returncode == 0, result.stdout + result.stderr
    heartbeat_lines = [line for line in result.stdout.splitlines() if line.startswith("[gate:")]
    assert heartbeat_lines
    assert any("backend: backend lane working" in line for line in heartbeat_lines)


def test_quick_gate_derives_backend_lane_from_worktree_changes(tmp_path: Path) -> None:
    quick_gate = _quick_gate_fixture(tmp_path / "scripts")
    _init_git_repo(tmp_path)
    backend_file = tmp_path / "server" / "app" / "foo.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("", encoding="utf-8")

    result = _run(quick_gate, cwd=tmp_path, env={})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Derived lanes from worktree changes: backend\n" in result.stdout
    assert "Parallel quick gate passed" in result.stdout


def test_quick_gate_derives_static_phase_for_docs_only_changes(tmp_path: Path) -> None:
    quick_gate = _quick_gate_fixture(tmp_path / "scripts")
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# fixture\n", encoding="utf-8")

    result = _run(quick_gate, cwd=tmp_path, env={})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Derived lanes from worktree changes: static\n" in result.stdout
    assert "Parallel quick gate passed" in result.stdout


def test_quick_gate_explicit_lanes_skip_derivation(tmp_path: Path) -> None:
    quick_gate = _quick_gate_fixture(tmp_path / "scripts")
    _init_git_repo(tmp_path)

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_LANES": "backend"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Derived lanes" not in result.stdout


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
            "GATE_TIER": "unit",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PostgreSQL offline" in result.stdout
    calls = gate_log.read_text(encoding="utf-8")
    assert "not postgres and not repository_gate" in calls
    assert "--durations=30" in calls
    assert f"--junitxml={results / 'quick-junit.xml'}" in calls
    assert "-p scripts.pytest_telemetry" in calls
    assert f"rerun:{results / 'quick-reruns.json'}" in calls


def test_backend_smoke_tier_runs_the_curated_subset(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'printf "db:%s\\n" "${AGENT_LEGION_TEST_DATABASE_URL:-unset}" >>"$GATE_LOG"\n',
    )

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "smoke",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Python Smoke Tests" in result.stdout
    calls = gate_log.read_text(encoding="utf-8")
    assert "-m smoke" in calls
    assert "not postgres" not in calls
    # The curated tier includes PostgreSQL-backed tests, so it must not be
    # pinned to the unit tier's unreachable database URL.
    assert "agent_legion_unit_offline" not in calls


def test_backend_full_coverage_defers_floor_to_combined_report(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$GATE_LOG"\n',
    )

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "AGENT_LEGION_COV": "1",
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "full",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "--cov=server" in calls
    assert "--cov-fail-under=0" in calls


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
    assert "run test -- --maxWorkers=4 --reporter=default --reporter=junit" in calls
    assert f"--outputFile.junit={results / 'vitest-junit.xml'}" in calls
    assert "--reporter=json" in calls
    assert f"--outputFile.json={results / 'vitest-results.json'}" in calls


def test_frontend_gate_shards_project_and_defers_coverage_enforcement(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    frontend = tmp_path / "frontend"
    fake_bin = tmp_path / "bin"
    results = tmp_path / "results"
    blobs = tmp_path / "blobs"
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
            "FRONTEND_COVERAGE_BLOB_DIR": str(blobs),
            "FRONTEND_GATE_PHASE": "test",
            "FRONTEND_TEST_MODE": "coverage",
            "FRONTEND_TEST_PROJECT": "logic",
            "GATE_LOG": str(gate_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "run test:coverage -- --maxWorkers=4 --project logic" in calls
    assert "--reporter=blob" in calls
    assert f"--outputFile.blob={blobs / 'vitest-blob-logic.json'}" in calls
    # A shard's partial coverage cannot meet the global thresholds; the merge
    # job enforces them once against the combined data instead.
    assert "--coverage.thresholds.lines=0" in calls
    assert "--coverage.thresholds.functions=0" in calls
    assert "--coverage.thresholds.branches=0" in calls
    assert "--coverage.thresholds.statements=0" in calls
    assert "run test:coverage-inventory" not in calls


def _run_backend_gate_with_fake_uv(tmp_path: Path, env: dict[str, str]) -> str:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'printf "shard:%s\\n" "${GATE_SHARD:-unset}" >>"$GATE_LOG"\n',
    )

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "postgres",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            **env,
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    return gate_log.read_text(encoding="utf-8")


def test_backend_postgres_tier_loads_shard_plugin_when_gate_shard_set(tmp_path: Path) -> None:
    calls = _run_backend_gate_with_fake_uv(tmp_path, {"GATE_SHARD": "1/3"})

    assert "-p scripts.pytest_gate_shard" in calls
    assert "shard:1/3" in calls
    assert "postgres and not repository_gate" in calls


def test_backend_postgres_tier_has_no_shard_plugin_without_gate_shard(tmp_path: Path) -> None:
    calls = _run_backend_gate_with_fake_uv(tmp_path, {})

    assert "scripts.pytest_gate_shard" not in calls
    assert "shard:unset" in calls
    assert "postgres and not repository_gate" in calls
