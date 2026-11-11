"""Workflow contract: real sandbox tests need the Linux backend setup first."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.no_db, pytest.mark.ci_extended]

ROOT = Path(__file__).resolve().parents[2]
INSTALL_STEP = "Install bubblewrap (velites sandbox backend)"
EXPECTED_TEST_STEPS = {
    ("backend-unit", "Backend unit tests (PostgreSQL offline)"),
    ("backend-postgres", "Velites sandbox integration"),
    ("backend-postgres", "Backend PostgreSQL tests (shard ${{ matrix.gate_shard }})"),
    ("backend-postgres", "Full gate evidence (tests/full)"),
    ("e2e-smoke", "Browser smoke E2E"),
    ("rust", "cargo test"),
}
DIRECT_SANDBOX_COMMANDS = (
    "test_make_workflow_worker_runs_demo_intake_local_node",
    "uv run pytest -q tests/full",
    "run_browser_smoke.py",
    "cargo test --locked",
)


def _runs_sandbox_tests(step: dict[str, Any]) -> bool:
    run = step.get("run", "")
    if (
        step.get("env", {}).get("GATE_TIER") in {"unit", "postgres"}
        and "check-quick-backend.sh" in run
    ):
        return True
    return any(command in run for command in DIRECT_SANDBOX_COMMANDS)


def test_every_sandbox_test_step_installs_and_enables_bubblewrap_first() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/quality-gate.yml").read_text(encoding="utf-8")
    )
    tested_steps: set[tuple[str, str]] = set()

    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        install_indexes = [
            index for index, step in enumerate(steps) if step.get("name") == INSTALL_STEP
        ]
        for index, step in enumerate(steps):
            if not _runs_sandbox_tests(step):
                continue
            step_name = step["name"]
            tested_steps.add((job_name, step_name))
            assert len(install_indexes) == 1, (
                f"{job_name}/{step_name} needs exactly one {INSTALL_STEP!r} step"
            )
            install_index = install_indexes[0]
            assert install_index < index, f"{job_name}/{step_name} runs before sandbox setup"
            install_run = steps[install_index]["run"]
            assert "apt-get install -y bubblewrap" in install_run
            assert "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in install_run

    assert tested_steps == EXPECTED_TEST_STEPS
