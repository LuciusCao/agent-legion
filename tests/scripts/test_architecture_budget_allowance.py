"""Tests for the #641 growth-allowance band.

The band lets effective lines exceed a registry ceiling by a fixed amount
without error, and — unlike buffer_lines — the overshoot is never absorbed
into the registry: the ratchet still only lowers, so a file that shrinks
back releases the band and a file that stays big keeps paying for it in
review visibility.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from scripts.architecture.budget_policy import (
    BudgetConfigurationError,
    load_budget_policy,
)
from scripts.architecture.exemptions import load_exemptions
from scripts.architecture.file_budgets import check_file_budgets
from scripts.ratchet_architecture_budgets import ratchet_budgets
from tests.architecture_budget_helpers import (
    governed_repo,
    rewrite_exemption_ceiling,
    write_baseline,
)

pytestmark = pytest.mark.no_db

VALID_POLICY = {
    "version": 1,
    "production": {
        "roots": [
            {"path": "server/app", "extensions": [".py"]},
        ],
        "exclude": [],
        "buffer_lines": 5,
        "max_lines": 800,
    },
    "tests": {
        "roots": [
            {"path": "tests", "patterns": ["**/*.py"]},
        ],
        "max_lines": 1000,
    },
}


class TestGrowthAllowancePolicy:
    def test_absent_allowance_defaults_to_strict_zero(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(VALID_POLICY), encoding="utf-8")

        assert load_budget_policy(policy_path).growth_allowance == 0

    def test_explicit_allowance_loads(self, tmp_path: Path) -> None:
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["growth_allowance"] = 15
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        assert load_budget_policy(policy_path).growth_allowance == 15

    @pytest.mark.parametrize("value", [-1, True, "15", 1.5])
    def test_invalid_allowance_rejected(self, tmp_path: Path, value: object) -> None:
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["growth_allowance"] = value
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        with pytest.raises(BudgetConfigurationError, match="growth_allowance"):
            load_budget_policy(policy_path)


class TestGrowthAllowanceCeilingCheck:
    def test_within_band_overshoot_passes(self, tmp_path: Path) -> None:
        root, policy = governed_repo(tmp_path, "server/app/example.py", lines=40)
        write_baseline(root, {"server/app/example.py": 25})

        assert check_file_budgets(root, replace(policy, growth_allowance=15), ()) == []

    def test_one_line_beyond_band_errors(self, tmp_path: Path) -> None:
        root, policy = governed_repo(tmp_path, "server/app/example.py", lines=41)
        write_baseline(root, {"server/app/example.py": 25})

        assert check_file_budgets(root, replace(policy, growth_allowance=15), ()) == [
            "server/app/example.py: 41 effective lines exceeds ceiling 25 "
            "+ growth allowance 15; split the file, re-file the exemption, "
            "or revert growth"
        ]

    def test_zero_allowance_keeps_legacy_message(self, tmp_path: Path) -> None:
        root, policy = governed_repo(tmp_path, "server/app/example.py", lines=26)
        write_baseline(root, {"server/app/example.py": 25})

        assert check_file_budgets(root, policy, ()) == [
            "server/app/example.py: 26 effective lines exceeds ceiling 25; "
            "split the file or revert growth"
        ]

    def test_exempt_file_within_band_passes(self, tmp_path: Path) -> None:
        root, policy = governed_repo(tmp_path, "server/app/example.py", lines=60)
        write_baseline(root, {"server/app/example.py": 25})
        rewrite_exemption_ceiling(root, ceiling=50)

        exemptions = load_exemptions(root)
        assert check_file_budgets(root, replace(policy, growth_allowance=15), exemptions) == []

    def test_exempt_file_beyond_band_errors(self, tmp_path: Path) -> None:
        root, policy = governed_repo(tmp_path, "server/app/example.py", lines=66)
        write_baseline(root, {"server/app/example.py": 25})
        rewrite_exemption_ceiling(root, ceiling=50)

        exemptions = load_exemptions(root)
        assert check_file_budgets(root, replace(policy, growth_allowance=15), exemptions) == [
            "server/app/example.py: 66 effective lines exceeds ceiling 50 "
            "+ growth allowance 15; split the file, re-file the exemption, "
            "or revert growth"
        ]


def _allowance_repo(tmp_path: Path, *, lines: int, baseline_ceiling: int, allowance: int) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    policy = copy.deepcopy(VALID_POLICY)
    policy["production"]["growth_allowance"] = allowance
    (root / "config/architecture").mkdir(parents=True)
    (root / "config/architecture/architecture-budget-policy.yaml").write_text(
        yaml.safe_dump(policy), encoding="utf-8"
    )
    (root / "config/architecture/architecture-budgets.json").write_text(
        json.dumps({"version": 3, "files": {"server/app/example.py": baseline_ceiling}}),
        encoding="utf-8",
    )
    (root / "server/app").mkdir(parents=True)
    (root / "server/app/example.py").write_text(
        "\n".join(f"x_{idx} = {idx}" for idx in range(lines)), encoding="utf-8"
    )
    return root


class TestGrowthAllowanceRatchet:
    def test_within_band_overshoot_is_never_absorbed(self, tmp_path: Path) -> None:
        root = _allowance_repo(tmp_path, lines=40, baseline_ceiling=25, allowance=15)

        result = ratchet_budgets(root)

        assert result.errors == ()
        assert result.changed is False
        baseline = json.loads((root / "config/architecture/architecture-budgets.json").read_text())
        assert baseline["files"] == {"server/app/example.py": 25}

    def test_beyond_band_errors_with_allowance_message(self, tmp_path: Path) -> None:
        root = _allowance_repo(tmp_path, lines=41, baseline_ceiling=25, allowance=15)

        result = ratchet_budgets(root)

        assert len(result.errors) == 1
        assert "exceeds ceiling 25 + growth allowance 15" in result.errors[0]
        assert result.changed is False
