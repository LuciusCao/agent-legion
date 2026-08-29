from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        "AGENT_LEGION_COV",
        "AGENT_LEGION_FRONTEND_TEST_WORKERS",
        "AGENT_LEGION_RUST_WORKERS",
        "AGENT_LEGION_TEST_WORKERS",
        "BACKEND_GATE_PHASE",
        "BACKEND_SKIP_WORKER_UI_TESTS",
        "COVERAGE_FILE",
        "FRONTEND_API_CHECK",
        "FRONTEND_COVERAGE_BLOB_DIR",
        "FRONTEND_GATE_PHASE",
        "FRONTEND_TEST_MODE",
        "FRONTEND_TEST_PROJECT",
        "GATE_LANES",
        "GATE_SHARD",
        "GATE_SKIP_STATIC",
        "GATE_TIER",
        "KEEP_COVERAGE",
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
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
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
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
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
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
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


def test_quick_gate_skip_static_runs_test_round_only(tmp_path: Path) -> None:
    """GATE_SKIP_STATIC=1 (check.sh's postgres segment) re-enters the gate for
    the test rounds alone: no lane may run the static phase again, and the
    api-contract integration step between the rounds is skipped too — the
    unit segment already ran those checks."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    gate_log = tmp_path / "gate.log"

    _write_executable(
        scripts / "check-quick-backend.sh",
        '#!/usr/bin/env bash\nprintf "backend:%s\\n" "${BACKEND_GATE_PHASE:-unset}" >>"$GATE_LOG"\n',
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        '#!/usr/bin/env bash\nprintf "frontend:%s\\n" "${FRONTEND_GATE_PHASE:-unset}" >>"$GATE_LOG"\n',
    )

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={
            "GATE_LANES": "backend frontend",
            "GATE_LOG": str(gate_log),
            "GATE_SKIP_STATIC": "1",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Static round skipped" in result.stdout
    calls = gate_log.read_text(encoding="utf-8").splitlines()
    # Only the staggered test rounds run: backend first, frontend after —
    # no static phase, no api-contract integration step.
    assert calls == ["backend:test", "frontend:test"]


def test_quick_gate_staggers_backend_test_round_before_frontend_and_rust(tmp_path: Path) -> None:
    """Test-round staggering (PR #225): the backend test lane runs alone
    first; frontend/rust only start once it finished. Starting all three
    together oversubscribed the machine from inside the gate. The frontend
    stub appends a marker after backend's marker only if the file exists —
    its mere success proves the backend round completed first."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    order_log = tmp_path / "order.log"

    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${BACKEND_GATE_PHASE:-}" == "test" ]]; then\n'
        '  echo "backend-test-start" >>"$ORDER_LOG"\n'
        '  echo "backend-test-end" >>"$ORDER_LOG"\n'
        "fi\n",
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${FRONTEND_GATE_PHASE:-}" == "test" ]]; then\n'
        '  echo "frontend-test-start" >>"$ORDER_LOG"\n'
        "fi\n",
    )

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={"GATE_LANES": "backend frontend", "ORDER_LOG": str(order_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    order = order_log.read_text(encoding="utf-8").splitlines()
    # No interleaving: the frontend test lane starts strictly after the
    # backend test lane finished (the static round stays parallel but its
    # stubs emit no markers).
    assert order == ["backend-test-start", "backend-test-end", "frontend-test-start"]


def test_quick_gate_backend_test_failure_still_runs_frontend_round(tmp_path: Path) -> None:
    """A failing backend test round must not skip the remaining lanes: every
    lane still reports, the failure is aggregated, and the gate exits 1."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    phase_log = tmp_path / "phase.log"

    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        'printf "backend:%s\\n" "${BACKEND_GATE_PHASE:-unset}" >>"$PHASE_LOG"\n'
        '[[ "${BACKEND_GATE_PHASE:-}" != "test" ]] || exit 7\n',
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        '#!/usr/bin/env bash\nprintf "frontend:%s\\n" "${FRONTEND_GATE_PHASE:-unset}" >>"$PHASE_LOG"\n',
    )

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={"GATE_LANES": "backend frontend", "PHASE_LOG": str(phase_log)},
    )

    assert result.returncode == 1
    # Both rounds ran the backend lane; the frontend test round still ran
    # after the backend test failure instead of being skipped (the api-contract
    # integration step between the rounds is frontend:api-contract).
    phases = phase_log.read_text(encoding="utf-8").splitlines()
    assert phases == [
        "backend:static",
        "frontend:static",
        "frontend:api-contract",
        "backend:test",
        "frontend:test",
    ]
    assert "backend=7 frontend=0" in result.stderr
    assert "Test round failed" in result.stderr


def test_quick_gate_staggered_rounds_stay_silent_for_empty_phases(tmp_path: Path) -> None:
    """An empty phase (the lane runs in another round of this gate) must stay
    silent; only a GATE_LANES trim announces its skips. Without this the
    staggered two-round shape would spam six extra "Skipping" lines per
    full-lane gate."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    _write_executable(scripts / "check-quick-backend.sh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(scripts / "check-quick-frontend.sh", "#!/usr/bin/env bash\nexit 0\n")

    full_lanes = _run(quick_gate, cwd=tmp_path, env={})
    assert full_lanes.returncode == 0, full_lanes.stdout + full_lanes.stderr
    # All lanes enabled: the staggered test rounds run no "Skipping" lines
    # (each lane runs in exactly one of the two rounds).
    assert "Skipping" not in full_lanes.stdout

    trimmed = _run(quick_gate, cwd=tmp_path, env={"GATE_LANES": "backend"})
    assert trimmed.returncode == 0, trimmed.stdout + trimmed.stderr
    # A GATE_LANES trim still announces every skipped lane in every round
    # (frontend/rust: static + test-backend + test-rest; backend runs in
    # both of its rounds, so it is never announced).
    assert trimmed.stdout.count("Skipping backend") == 0
    assert trimmed.stdout.count("Skipping frontend") == 3
    assert trimmed.stdout.count("Skipping rust") == 3


def _quick_gate_fixture(scripts: Path) -> Path:
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
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
    # The quick gate sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
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
        "#!/usr/bin/env bash\n"
        'printf "quick:lanes=%s,cov=%s,mode=%s,tier=%s,append=%s,skip_static=%s,skip_worker_ui=%s\\n" '
        '"${GATE_LANES:-unset}" "${AGENT_LEGION_COV:-unset}" '
        '"${FRONTEND_TEST_MODE:-unset}" "${GATE_TIER:-unset}" '
        '"${AGENT_LEGION_COV_APPEND:-0}" '
        '"${GATE_SKIP_STATIC:-unset}" "${BACKEND_SKIP_WORKER_UI_TESTS:-unset}" >>"$GATE_LOG"\n',
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
    quick_calls = [call for call in calls if call.startswith("quick:")]
    # Issue #92: the coverage-instrumented backend lane runs alone first —
    # racing the frontend/rust lanes makes pytest-cov/xdist silently lose a
    # whole worker's coverage data. Frontend/rust follow without backend
    # coverage (frontend still runs in coverage mode for the partition report).
    # The backend segments run the unit and postgres tiers separately (the
    # quick gate's full tier is unit-only) and append coverage onto the same
    # COVERAGE_FILE — the append flag is asserted because a silent loss of
    # that flag would make the postgres segment erase the unit segment's
    # data instead of extending it, and the combined report would quietly
    # under-count rather than fail loudly.
    # The postgres segment re-enters the quick gate for its test round only
    # (GATE_SKIP_STATIC=1, BACKEND_SKIP_WORKER_UI_TESTS=1): segment 1a already
    # ran the static checks and the tier-independent worker UI tests, so every
    # check still runs exactly once per full gate.
    assert quick_calls == [
        "quick:lanes=backend,cov=1,mode=unset,tier=unit,append=0,skip_static=unset,skip_worker_ui=unset",
        "quick:lanes=backend,cov=1,mode=unset,tier=postgres,append=1,skip_static=1,skip_worker_ui=1",
        "quick:lanes=frontend rust,cov=unset,mode=coverage,tier=unset,append=0,skip_static=unset,skip_worker_ui=unset",
    ]
    assert calls.count("npm:run build:bundle") == 1
    assert not any("test:coverage" in call for call in calls)
    assert any("pytest -q tests/full" in call for call in calls)
    assert any("coverage report" in call for call in calls)


def test_quick_gate_cleanup_only_removes_coverage_data_files(tmp_path: Path) -> None:
    """The cleanup glob must match coverage's parallel suffix
    (.<host>.<pid>.<random>) only — not every same-prefix sibling."""
    quick_gate = _quick_gate_fixture(tmp_path / "scripts")
    cov = tmp_path / ".coverage.fixture"
    cov.write_text("combined", encoding="utf-8")
    worker_data = tmp_path / ".coverage.fixture.host.1234.000042"
    worker_data.write_text("worker", encoding="utf-8")
    sibling = tmp_path / ".coverage.fixture.log"
    sibling.write_text("keep", encoding="utf-8")

    result = _run(quick_gate, cwd=tmp_path, env={"COVERAGE_FILE": str(cov)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert not cov.exists()
    assert not worker_data.exists()
    assert sibling.exists()


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


def _rust_gate_fixture(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    # The rust lane requires the crate directory to exist.
    (tmp_path / "velites").mkdir()
    _write_executable(
        fake_bin / "cargo",
        '#!/usr/bin/env bash\nprintf "cargo:%s\\n" "$*" >>"$GATE_LOG"\n',
    )
    return quick_gate, fake_bin


def _run_rust_gate(tmp_path: Path, env: dict[str, str]) -> str:
    quick_gate, fake_bin = _rust_gate_fixture(tmp_path)
    gate_log = tmp_path / "gate.log"
    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={
            "GATE_LANES": "rust",
            "GATE_LOG": str(gate_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            **env,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return gate_log.read_text(encoding="utf-8")


def test_rust_lane_workers_default_is_capped(tmp_path: Path) -> None:
    calls = _run_rust_gate(tmp_path, {})

    clippy = re.search(r"cargo:clippy .* -j (\d+)", calls)
    assert clippy is not None, calls
    assert 1 <= int(clippy.group(1)) <= 8
    assert re.search(r"cargo:test --locked -j \d+", calls)


def test_rust_lane_workers_env_override_wins(tmp_path: Path) -> None:
    calls = _run_rust_gate(tmp_path, {"AGENT_LEGION_RUST_WORKERS": "3"})

    assert re.search(r"cargo:clippy .* -j 3 ", calls)
    assert "cargo:test --locked -j 3" in calls
