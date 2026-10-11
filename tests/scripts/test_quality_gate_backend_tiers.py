"""Contract tests for scripts/check-quick-backend.sh (backend gate lane).

Every tier of the backend test lane is pinned here against a fake uv that
logs its arguments: marker selections, offline database pinning, xdist
worker distribution, telemetry artifacts, and the aff index fallbacks.
Orchestrator-level behavior (rounds, staggering, lane trimming) lives in
tests/scripts/test_quality_gate_scripts.py.

Fixture discipline: the read-only majority runs against ONE module-scoped
layout (backend_gate_layout) whose superset fake uv logs every observation
channel (args / shard / db / rerun) at once — each run's behavior depends
only on env and tier, both injected per case, and GATE_LOG points at the
case's own tmp_path, so sharing the scripts copies and the stub cannot leak
between cases. Cases that MUTATE the layout (writing .pytest-aff-index.json,
adding worker/ui, replacing the uv stub with a selection script) keep their
own per-case layouts below; they are exactly the aff-selection trio, the
worker-ui skip case and the frontend lane case.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


# Superset fake uv: logs every observation channel the read-only cases assert
# on, so one stub serves all of them (per-case stubs only differed in which
# channels they recorded, never in behavior).
_SUPERSET_UV_STUB = (
    "#!/usr/bin/env bash\n"
    'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
    'printf "shard:%s\\n" "${GATE_SHARD:-unset}" >>"$GATE_LOG"\n'
    'printf "db:%s\\n" "${AGENT_LEGION_TEST_DATABASE_URL:-unset}" >>"$GATE_LOG"\n'
    'printf "rerun:%s\\n" "${AGENT_LEGION_RERUN_REPORT:-unset}" >>"$GATE_LOG"\n'
)


@pytest.fixture(scope="module")
def backend_gate_layout(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Module-scoped read-only layout: the real backend gate script, its two
    sourced helpers and the superset fake uv. Read-only means: no case may
    add/replace files under this root (the aff-index file and worker/ui live
    at ROOT_DIR, so those cases build their own layouts) — the gate itself
    only rm -f's its own transient .pytest-aff-coverage here, which no case
    asserts on."""
    root = tmp_path_factory.mktemp("backend-gate")
    scripts = root / "scripts"
    fake_bin = root / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", scripts)
    # The backend lane sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts)
    _write_executable(fake_bin / "uv", _SUPERSET_UV_STUB)
    return root, scripts, fake_bin


def _run_shared_backend_gate(
    layout: tuple[Path, Path, Path],
    tmp_path: Path,
    env: dict[str, str],
    *,
    capture: str = "log",
) -> tuple[str, str] | str:
    """Run the shared-layout backend gate with per-case env/tier.

    ``capture="log"`` returns the fake-uv argument log; ``capture="both"``
    returns ``(log, stdout)`` for tiers whose routing messages only appear on
    stdout. GATE_LOG stays per-case (tmp_path), so concurrent-module cases
    never share observation state.
    """
    root, scripts, fake_bin = layout
    gate_log = tmp_path / "gate.log"
    result = _run(
        scripts / "check-quick-backend.sh",
        cwd=root,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "postgres",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            **env,
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    if capture == "both":
        return gate_log.read_text(encoding="utf-8"), result.stdout
    return gate_log.read_text(encoding="utf-8")


def _run(path: Path, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    for key in (
        "AGENT_LEGION_TEST_DATABASE_URL",
        "AGENT_LEGION_COV",
        "AGENT_LEGION_FRONTEND_TEST_WORKERS",
        "AGENT_LEGION_RUST_WORKERS",
        "AGENT_LEGION_TEST_WORKERS",
        "BACKEND_GATE_PHASE",
        "BACKEND_SKIP_WORKER_UI_TESTS",
        "CI",
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


def test_backend_gate_emits_junit_durations_and_rerun_report(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    results = tmp_path / "results"
    calls, stdout = _run_shared_backend_gate(
        backend_gate_layout,
        tmp_path,
        {
            "AGENT_LEGION_TEST_RESULTS_DIR": str(results),
            "AGENT_LEGION_TEST_RESULT_NAME": "quick",
            "GATE_TIER": "unit",
        },
        capture="both",
    )

    assert "PostgreSQL offline" in stdout
    assert "-m not postgres" in calls
    assert "--durations=30" in calls
    assert f"--junitxml={results / 'quick-junit.xml'}" in calls
    assert "-p scripts.pytest_telemetry" in calls
    assert f"rerun:{results / 'quick-reruns.json'}" in calls


def test_backend_coverage_args_include_worker_for_partition_floor(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """Issue #275: the AGENT_LEGION_COV path must also collect --cov=worker.
    CI's backend-unit and backend-postgres shards run these tiers with
    AGENT_LEGION_COV=1 and upload the data files; backend-coverage then
    enforces the worker execution-plane partition floor on the combined
    data. Losing --cov=worker here would make that partition see NO DATA
    (a violation in enforce mode only after the shards shipped empty worker
    data — better to pin the invocation itself)."""
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"GATE_TIER": "unit", "AGENT_LEGION_COV": "1"}
    )

    assert "--cov=server" in calls
    assert "--cov=worker" in calls
    assert "--cov-report=term-missing" in calls
    # The floor stays deferred: per-shard data is partial by construction.
    assert "--cov-fail-under=0" in calls


def test_backend_smoke_tier_runs_the_curated_subset(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls, stdout = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"GATE_TIER": "smoke"}, capture="both"
    )

    assert "Python Smoke Tests" in stdout
    assert "-m smoke" in calls
    assert "not postgres" not in calls
    # The curated tier includes PostgreSQL-backed tests, so it must not be
    # pinned to the unit tier's unreachable database URL.
    assert "agent_legion_unit_offline" not in calls


def test_backend_gate_can_skip_worker_ui_tests(tmp_path: Path) -> None:
    """BACKEND_SKIP_WORKER_UI_TESTS=1 (check.sh's postgres segment) skips the
    node --test run: the unit segment already ran the identical invocation,
    and the tier selection cannot affect these tier-independent tests."""

    def run_backend_gate(workdir: Path, extra_env: dict[str, str]) -> tuple[str, str]:
        scripts = workdir / "scripts"
        fake_bin = workdir / "bin"
        scripts.mkdir(parents=True)
        fake_bin.mkdir(parents=True)
        backend_gate = scripts / "check-quick-backend.sh"
        shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
        # The backend lane sources the shared job-count helper.
        shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
        shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
        # The worker/ui test file must exist, or the suite is skipped for an
        # unrelated reason (fixture repos without the worker console).
        (workdir / "worker" / "ui").mkdir(parents=True)
        (workdir / "worker" / "ui" / "app.test.mjs").write_text("", encoding="utf-8")
        gate_log = workdir / "gate.log"
        _write_executable(
            fake_bin / "uv",
            '#!/usr/bin/env bash\nprintf "uv:%s\\n" "$*" >>"$GATE_LOG"\n',
        )
        _write_executable(
            fake_bin / "node",
            '#!/usr/bin/env bash\nprintf "node:%s\\n" "$*" >>"$GATE_LOG"\n',
        )

        result = _run(
            backend_gate,
            cwd=workdir,
            env={
                "BACKEND_GATE_PHASE": "test",
                "GATE_LOG": str(gate_log),
                "GATE_TIER": "postgres",
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                **extra_env,
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return gate_log.read_text(encoding="utf-8"), result.stdout

    control_calls, _ = run_backend_gate(tmp_path / "run", {})
    assert "node:--test" in control_calls

    skipped_calls, skipped_stdout = run_backend_gate(
        tmp_path / "skip", {"BACKEND_SKIP_WORKER_UI_TESTS": "1"}
    )
    assert "node:" not in skipped_calls
    assert "skipped: BACKEND_SKIP_WORKER_UI_TESTS=1" in skipped_stdout
    # The tier's own pytest run is untouched.
    assert "uv:run --frozen pytest" in skipped_calls


def test_backend_full_coverage_defers_floor_to_combined_report(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"AGENT_LEGION_COV": "1", "GATE_TIER": "full"}
    )

    assert "--cov=server" in calls
    assert "--cov=worker" in calls
    assert "--cov-fail-under=0" in calls
    # The full tier is the unit layer (PR #225): same marker selection as
    # GATE_TIER=unit, same unreachable offline database URL (the URL travels
    # via the environment, so the fake uv logs it separately). Without these
    # assertions a revert to the whole quick suite would pass silently while
    # the AGENTS.md discipline assumes the unit-tier default.
    assert "-m not postgres" in calls
    assert "agent_legion_unit_offline" in calls


def test_backend_governance_phase_runs_repo_wide_checks_only(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """BACKEND_GATE_PHASE=governance (issue #1201) runs the repo-wide
    governance set — the checks whose scope spans every lane — and nothing
    lane-bound: ruff/mypy/pytest stay with the backend lane's own phases."""
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"BACKEND_GATE_PHASE": "governance"}
    )

    assert "run --frozen python -m scripts.check_invariants" in calls
    assert "run --frozen python -m scripts.check_versions" in calls
    assert "run --frozen python -m scripts.check_architecture" in calls
    assert "run --frozen python -m scripts.generate_architecture --check" in calls
    assert "ruff" not in calls
    assert "mypy" not in calls
    assert "pytest" not in calls


def test_backend_governance_phase_aggregates_failures(tmp_path: Path) -> None:
    """A failing governance check must not mask the rest of the set: every
    section runs and reports before the phase exits non-zero (fail-fast
    ordering masked a second budget violation on PR #1177). Per-case layout:
    replacing the uv stub mutates the layout, which the shared fixture's
    read-only contract forbids."""
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    # The backend lane sources the shared job-count helper.
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'case " $* " in\n'
        '  *" scripts.check_invariants "*) exit 1 ;;\n'
        "esac\n",
    )

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "governance",
            "GATE_LOG": str(gate_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 1
    calls = gate_log.read_text(encoding="utf-8")
    assert "run --frozen python -m scripts.check_invariants" in calls
    # The later checks still ran despite the first one's failure.
    assert "run --frozen python -m scripts.check_architecture" in calls
    assert "run --frozen python -m scripts.generate_architecture --check" in calls
    assert "Repo-wide governance checks failed" in result.stderr


def test_backend_static_phase_still_includes_governance_checks(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """The governance set stays inside the backend lane's static phase: the
    governance phase and CI's governance-guard job are its complement for
    trimmed lanes, not a move — dropping it here would leave backend-lane
    gates with no budget enforcement at all."""
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"BACKEND_GATE_PHASE": "static"}
    )

    assert "run --frozen ruff check ." in calls
    assert "run --frozen mypy" in calls
    assert "run --frozen python -m scripts.check_invariants" in calls
    assert "run --frozen python -m scripts.check_versions" in calls
    assert "run --frozen python -m scripts.check_architecture" in calls
    assert "run --frozen python -m scripts.generate_architecture --check" in calls


def test_backend_aff_tier_falls_back_to_unit_without_index(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """GATE_TIER=aff without .pytest-aff-index.json must run the whole unit
    tier against the offline database URL (fallback only widens what runs).
    Read-only shared layout is safe here: the shared root never gains an
    index file (the with-index cases below build their own layouts)."""
    calls, stdout = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"GATE_TIER": "aff"}, capture="both"
    )

    assert "aff fallback: no .pytest-aff-index.json" in stdout
    assert "-m not postgres" in calls
    assert "agent_legion_unit_offline" in calls


def test_backend_aff_tier_selects_tests_with_index(tmp_path: Path) -> None:
    """With an index present, the aff tier passes the selected nodeids to
    pytest instead of the whole-tier marker filter."""
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    gate_log = tmp_path / "gate.log"
    # The fake uv logs its arguments; the selection subcommand prints one
    # selected nodeid the gate must forward to pytest.
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >>"$GATE_LOG"\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == "scripts.pytest_aff_selection" ]]; then\n'
        '    if [[ "${prev:-}" == "-m" ]]; then\n'
        '      printf "tests/test_fake.py::test_thing\\n"\n'
        "    fi\n"
        "  fi\n"
        '  prev="$arg"\n'
        "done\n",
    )
    # The aff tier requires the index file to exist before it even asks for a
    # selection.
    (tmp_path / ".pytest-aff-index.json").write_text("{}", encoding="utf-8")

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "aff",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gate_log.read_text(encoding="utf-8")
    assert "Python Affected Tests (selected" in result.stdout
    # The selected nodeid is forwarded as a pytest path argument.
    assert "tests/test_fake.py::test_thing" in calls


def test_backend_aff_tier_falls_back_when_selection_is_broad(tmp_path: Path) -> None:
    """A selection covering hundreds of nodeids saves nothing over the unit
    tier; the gate must fall back to the plain unit invocation."""
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >>"$GATE_LOG"\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == "scripts.pytest_aff_selection" ]]; then\n'
        '    if [[ "${prev:-}" == "-m" ]]; then\n'
        '      for i in $(seq 1 500); do printf "tests/test_broad.py::test_%s\\n" "$i"; done\n'
        "    fi\n"
        "  fi\n"
        '  prev="$arg"\n'
        "done\n",
    )
    (tmp_path / ".pytest-aff-index.json").write_text("{}", encoding="utf-8")

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "aff",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "aff fallback: no index or selection too broad" in result.stdout
    calls = gate_log.read_text(encoding="utf-8")
    assert "-m\nnot postgres\n" in calls


def test_backend_aff_tier_falls_back_on_unmapped_source_files(tmp_path: Path) -> None:
    """Selection exit 4 (changed source file missing from the index) must
    run the full unit tier: the affected tests are unknown, and a selected
    subset would silently skip them (PR #184 Codex review)."""
    scripts = tmp_path / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    fake_bin.mkdir()
    backend_gate = scripts / "check-quick-backend.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick-backend.sh", backend_gate)
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    gate_log = tmp_path / "gate.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >>"$GATE_LOG"\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == "scripts.pytest_aff_selection" ]]; then\n'
        '    if [[ "${prev:-}" == "-m" ]]; then\n'
        '      echo "unmapped-source-files" >&2; exit 4\n'
        "    fi\n"
        "  fi\n"
        '  prev="$arg"\n'
        "done\n",
    )
    (tmp_path / ".pytest-aff-index.json").write_text("{}", encoding="utf-8")

    result = _run(
        backend_gate,
        cwd=tmp_path,
        env={
            "BACKEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "GATE_TIER": "aff",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "aff fallback: changed source files missing from the index" in result.stdout
    calls = gate_log.read_text(encoding="utf-8")
    assert "-m\nnot postgres\n" in calls


def test_backend_aff_index_tier_uses_coverage_contexts(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """The aff-index primer must trace per-test coverage contexts and build
    the index from the dedicated coverage file (not the default .coverage)."""
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {"GATE_TIER": "aff-index"})

    assert "--cov-context=test" in calls
    assert "--cov-report=" in calls
    assert "pytest_aff_selection build" in calls


def test_backend_postgres_tier_loads_shard_plugin_when_gate_shard_set(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {"GATE_SHARD": "1/3"})

    assert "-p scripts.pytest_gate_shard" in calls
    assert "shard:1/3" in calls
    assert "-m postgres" in calls


def test_backend_postgres_tier_has_no_shard_plugin_without_gate_shard(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {})

    assert "scripts.pytest_gate_shard" not in calls
    assert "shard:unset" in calls
    assert "-m postgres" in calls


def test_backend_test_workers_default_is_capped(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """The pytest -n default is worktree-aware (scripts/gate-jobs.sh): capped
    at min(4, cores) while a sibling worktree runs a gate, cores-2 (capped at
    8) otherwise. In the fake-repo fixture no sibling gate lock exists, so the
    idle branch must hold; the busy branch is covered by the gate-jobs.sh
    unit tests (issue #91 keeps the busy-branch cap at 4)."""
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {})

    match = re.search(r"(?:^|\s)-n (\d+)(?:\s|$)", calls)
    assert match is not None, calls
    assert 1 <= int(match.group(1)) <= 8


def test_backend_pytest_distributes_work_with_worksteal(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """Every xdist invocation uses --dist worksteal: the default `load`
    scheduler strands a slow test's whole batch on one worker while the rest
    idle, and that tail is where quick-gate wall time (and timeout flakes)
    came from. worksteal keeps idle workers stealing pending tests."""
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {})

    # Count the uv invocation ("run --frozen pytest ", issue #526), not the
    # "pytest" substring — telemetry mode (AGENT_LEGION_TEST_RESULTS_DIR, as
    # in CI) also passes "-p scripts.pytest_telemetry", which contains it.
    assert calls.count("run --frozen pytest ") == 1
    assert "--dist worksteal" in calls


def test_backend_test_workers_env_override_wins(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"AGENT_LEGION_TEST_WORKERS": "7"}
    )

    assert re.search(r"(?:^|\s)-n 7(?:\s|$)", calls)


def test_backend_postgres_tier_pins_one_worker_on_ci(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """#1150 D2: CI postgres shards run -n 1 — the loaded-runner timing family
    (10 of 16 flaky entries) lives on 2-core runners where -n 2 splits the
    same cores with postgres and coverage. Local runs keep the default."""
    calls = _run_shared_backend_gate(backend_gate_layout, tmp_path, {"CI": "true"})

    assert re.search(r"(?:^|\s)-n 1(?:\s|$)", calls)
    assert "-n 2" not in calls


def test_backend_postgres_tier_keeps_default_workers_without_ci(
    backend_gate_layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    calls = _run_shared_backend_gate(
        backend_gate_layout, tmp_path, {"AGENT_LEGION_TEST_WORKERS": "5"}
    )

    assert re.search(r"(?:^|\s)-n 5(?:\s|$)", calls)


def test_frontend_gate_workers_env_override_wins(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    frontend = tmp_path / "frontend"
    fake_bin = tmp_path / "bin"
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
            "AGENT_LEGION_FRONTEND_TEST_WORKERS": "2",
            "FRONTEND_GATE_PHASE": "test",
            "GATE_LOG": str(gate_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "run test -- --maxWorkers=2" in gate_log.read_text(encoding="utf-8")
