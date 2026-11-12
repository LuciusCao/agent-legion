"""Contract tests for the repo-wide governance guard (issue #1201).

The budget/invariant checks (check_invariants / check_versions /
check_architecture / generate_architecture) govern frontend, velites and docs
files too, but live in the backend lane's static phase. check-quick.sh runs
them via BACKEND_GATE_PHASE=governance whenever lane trimming disables the
backend lane, so a trimmed local gate can no longer stay green where CI's
governance-guard job would go red. The general quick-gate orchestration
(rounds, staggering, lane trimming) is pinned in
tests/scripts/test_quality_gate_scripts.py; the backend-side phase contract
lives in tests/scripts/test_quality_gate_backend_tiers.py.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _copy_lane_paths(scripts: Path) -> None:
    # check-quick.sh sources the shared lane path rules when it derives lanes
    # from the worktree (#941).
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", scripts / "lane-paths.sh")


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
        "GATE_SKIP_GOVERNANCE",
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


def _governance_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Quick-gate fixture whose lane stubs log every phase they are called with."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _copy_lane_paths(scripts)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    phase_log = tmp_path / "phase.log"
    _write_executable(
        scripts / "check-quick-backend.sh",
        '#!/usr/bin/env bash\nprintf "backend:%s\\n" "${BACKEND_GATE_PHASE:-unset}" >>"$PHASE_LOG"\n',
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        '#!/usr/bin/env bash\nprintf "frontend:%s\\n" "${FRONTEND_GATE_PHASE:-unset}" >>"$PHASE_LOG"\n',
    )
    return quick_gate, phase_log


def test_quick_gate_runs_governance_checks_when_backend_lane_trimmed(tmp_path: Path) -> None:
    """Issue #1201: the budget/invariant checks govern frontend files too but
    live in the backend lane's static phase — a gate trimmed to the frontend
    lane must still run them via the governance phase, otherwise local lanes
    stay green where CI's governance-guard job would go red."""
    quick_gate, phase_log = _governance_fixture(tmp_path)

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={"GATE_LANES": "frontend", "PHASE_LOG": str(phase_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    phases = phase_log.read_text(encoding="utf-8").splitlines()
    # The governance phase is the backend script's only call — the trimmed
    # lane never runs its own static/test phases.
    assert sorted(phases) == ["backend:governance", "frontend:static", "frontend:test"]
    assert "governance checks" in result.stdout


def test_quick_gate_runs_no_governance_phase_when_backend_lane_enabled(tmp_path: Path) -> None:
    """With the backend lane on, its static phase already includes the
    governance set — the guard must not double-run it."""
    quick_gate, phase_log = _governance_fixture(tmp_path)

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={"GATE_LANES": "backend", "PHASE_LOG": str(phase_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert phase_log.read_text(encoding="utf-8").splitlines() == ["backend:static", "backend:test"]


def test_quick_gate_static_lanes_run_governance_inside_backend_static(tmp_path: Path) -> None:
    """GATE_LANES=static (docs-only) enables every lane's static phase, so the
    backend static phase covers the governance set and the guard stays off."""
    quick_gate, phase_log = _governance_fixture(tmp_path)

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={"GATE_LANES": "static", "PHASE_LOG": str(phase_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    phases = phase_log.read_text(encoding="utf-8").splitlines()
    assert "backend:governance" not in phases
    assert sorted(phases) == ["backend:static", "frontend:api-contract", "frontend:static"]


def test_quick_gate_governance_failure_fails_the_gate(tmp_path: Path) -> None:
    """A failing governance guard must fail the gate with its diagnostics
    surfaced, not just a bare exit code."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _copy_lane_paths(scripts)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${BACKEND_GATE_PHASE:-}" == "governance" ]]; then\n'
        '  echo "budget blown"\n'
        "  exit 3\n"
        "fi\n",
    )
    _write_executable(scripts / "check-quick-frontend.sh", "#!/usr/bin/env bash\nexit 0\n")

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_LANES": "frontend"})

    assert result.returncode == 1
    assert "Repo-wide governance checks failed (status=3)" in result.stderr
    assert "budget blown" in result.stdout


def test_quick_gate_waits_for_governance_when_static_round_fails(tmp_path: Path) -> None:
    """A failing static lane must not let set -e kill the gate before the
    governance guard is collected: the guard would outlive the worktree lock
    and machine slot release, and its diagnostics would be lost (codex #1223
    R1). The gate waits for the guard, prints its output, then exits with
    the round's failure."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    quick_gate = scripts / "check-quick.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", quick_gate)
    _copy_lane_paths(scripts)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${BACKEND_GATE_PHASE:-}" == "governance" ]]; then\n'
        "  sleep 2\n"
        '  echo "governance-finished-marker"\n'
        "fi\n",
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        '#!/usr/bin/env bash\n[[ "${FRONTEND_GATE_PHASE:-}" != "static" ]] || exit 4\n',
    )

    result = _run(quick_gate, cwd=tmp_path, env={"GATE_LANES": "frontend"})

    assert result.returncode == 1
    assert "Parallel static-check round failed" in result.stderr
    # The guard's output was collected and printed despite the round failure —
    # without the wait, the gate would exit before the marker could land.
    assert "governance-finished-marker" in result.stdout


def test_quick_gate_skip_governance_env_suppresses_the_guard(tmp_path: Path) -> None:
    """GATE_SKIP_GOVERNANCE=1 (set only by check.sh's frontend+rust segment,
    whose backend segment already ran the set) keeps the guard off even with
    the backend lane trimmed."""
    quick_gate, phase_log = _governance_fixture(tmp_path)

    result = _run(
        quick_gate,
        cwd=tmp_path,
        env={
            "GATE_LANES": "frontend",
            "GATE_SKIP_GOVERNANCE": "1",
            "PHASE_LOG": str(phase_log),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    phases = phase_log.read_text(encoding="utf-8").splitlines()
    assert phases == ["frontend:static", "frontend:test"]
