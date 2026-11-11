"""Workflow contract: real sandbox tests need the validated Linux setup first."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.no_db, pytest.mark.ci_extended]

ROOT = Path(__file__).resolve().parents[2]
INSTALL_STEP = "Install bubblewrap (velites sandbox backend)"
VALIDATED_RUNNER = "ubuntu-24.04"
QUALITY_GATE_TEST_STEPS = {
    ("backend-unit", "Backend unit tests (PostgreSQL offline)"),
    ("backend-postgres", "Velites sandbox integration"),
    ("backend-postgres", "Backend PostgreSQL tests (shard ${{ matrix.gate_shard }})"),
    ("backend-postgres", "Full gate evidence (tests/full)"),
    ("e2e-smoke", "Browser smoke E2E"),
    ("rust", "cargo test"),
}
NIGHTLY_GATE_TEST_STEPS = {("nightly-e2e", "Multi-browser smoke E2E")}
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


@pytest.mark.parametrize(
    ("workflow_name", "expected_test_steps"),
    [
        pytest.param("quality-gate.yml", QUALITY_GATE_TEST_STEPS, id="quality-gate"),
        pytest.param("nightly-gate.yml", NIGHTLY_GATE_TEST_STEPS, id="nightly-gate"),
    ],
)
def test_every_sandbox_test_step_uses_the_validated_setup_first(
    workflow_name: str, expected_test_steps: set[tuple[str, str]]
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
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
            assert job["runs-on"] == VALIDATED_RUNNER, (
                f"{workflow_name}/{job_name} must use the validated sandbox runner"
            )
            assert len(install_indexes) == 1, (
                f"{job_name}/{step_name} needs exactly one {INSTALL_STEP!r} step"
            )
            install_index = install_indexes[0]
            assert install_index < index, f"{job_name}/{step_name} runs before sandbox setup"
            install_run = steps[install_index]["run"]
            assert "apt-get install -y bubblewrap" in install_run
            assert "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in install_run

    assert tested_steps == expected_test_steps


def test_sandbox_workflow_contract_runs_in_the_pr_unit_tier() -> None:
    gate = (ROOT / "scripts/check-quick-backend.sh").read_text(encoding="utf-8")
    unit_tier = gate.split("    unit)\n", 1)[1].split("    aff)\n", 1)[0]
    contract = "tests/ci/test_quality_gate_sandbox_setup.py"
    assert contract in unit_tier
    assert unit_tier.index(contract) < unit_tier.index(
        "=== Python Unit Tests (PostgreSQL offline) ==="
    )
