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
import subprocess
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.architecture.budget_policy import (
    BudgetConfigurationError,
    BudgetPolicy,
    load_budget_policy,
)
from scripts.architecture.exemptions import load_exemptions
from scripts.architecture.file_budgets import check_file_budgets
from scripts.quality.exemptions import (
    ArchitectureExemption,
    validate_exemptions,
)
from scripts.quality.exemptions import (
    load_exemptions as load_registry_exemptions,
)
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


_EXEMPTION_KWARGS = {
    "check": "architecture.file_budget",
    "path": "server/app/example.py",
    "reason": "Oversized module needs staged split.",
    "owner": "agent-legion",
    "remove_when": "issues/open/001.md",
}


def _exemption(**overrides: object) -> ArchitectureExemption:
    return ArchitectureExemption(**{**_EXEMPTION_KWARGS, **overrides})  # type: ignore[arg-type]


class TestExemptionExpires:
    def test_future_expires_passes(self, tmp_path: Path) -> None:
        exemption = _exemption(ceiling=100, expires="2027-01-01")

        assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == []

    @pytest.mark.parametrize("bad", ["2026-9-13", "01-01-2027", "not-a-date", ""])
    def test_malformed_expires_rejected(self, tmp_path: Path, bad: str) -> None:
        exemption = _exemption(ceiling=100, expires=bad)

        errors = validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13))

        assert any("must be an ISO date (YYYY-MM-DD)" in e for e in errors)

    def test_past_expires_hard_fails_with_renewal_guidance(self, tmp_path: Path) -> None:
        exemption = _exemption(ceiling=100, expires="2026-09-01")

        errors = validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13))

        assert any(
            "exemption expired on 2026-09-01 — renew it with a fresh justification" in e
            for e in errors
        )

    def test_today_is_still_valid(self, tmp_path: Path) -> None:
        exemption = _exemption(ceiling=100, expires="2026-09-13")

        assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == []

    def test_expires_optional(self, tmp_path: Path) -> None:
        exemption = _exemption(ceiling=100)

        assert validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13)) == []

    def test_unquoted_yaml_date_normalized_by_loader(self, tmp_path: Path) -> None:
        # codex P1 on #642: the documented plain form `expires: 2026-12-31`
        # is resolved by PyYAML to a datetime.date; the loader must normalize
        # it to its ISO string so the validator (and any consumer reading
        # exemption.expires) never sees a raw date object.
        registry = tmp_path / "architecture-exemptions.yaml"
        registry.write_text(
            "exemptions:\n"
            "- check: architecture.file_budget\n"
            "  path: server/app/example.py\n"
            "  reason: Oversized module needs staged split.\n"
            "  owner: agent-legion\n"
            "  remove_when: issues/open/001.md\n"
            "  ceiling: 200\n"
            "  expires: 2026-12-31\n",
            encoding="utf-8",
        )

        exemptions = load_registry_exemptions(registry)

        assert exemptions[0].expires == "2026-12-31"
        assert validate_exemptions(exemptions, tmp_path, today=date(2026, 9, 13)) == []

    def test_non_string_expires_rejected_not_traceback(self, tmp_path: Path) -> None:
        # Hand-constructed or malformed entries carrying a non-string
        # expires must fail validation with an error, never an AttributeError.
        exemption = _exemption(ceiling=100, expires=12345)

        errors = validate_exemptions((exemption,), tmp_path, today=date(2026, 9, 13))

        assert any("must be an ISO date string (YYYY-MM-DD)" in e for e in errors)


class TestExemptionCeilingAllowanceAlignment:
    def test_ceiling_below_band_lets_overshoot_pass_validation(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        (root / "server/app").mkdir(parents=True)
        (root / "server/app/example.py").write_text(
            "\n".join(f"x_{idx} = {idx}" for idx in range(60)), encoding="utf-8"
        )
        exemption = _exemption(ceiling=50)

        errors = validate_exemptions((exemption,), root, growth_allowance=15)

        assert not any("below actual" in e for e in errors)

    def test_ceiling_below_band_minus_one_still_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        (root / "server/app").mkdir(parents=True)
        (root / "server/app/example.py").write_text(
            "\n".join(f"x_{idx} = {idx}" for idx in range(66)), encoding="utf-8"
        )
        exemption = _exemption(ceiling=50)

        errors = validate_exemptions((exemption,), root, growth_allowance=15)

        assert any("even with the growth allowance (15 lines)" in e for e in errors)


def _refile_repo(tmp_path: Path, committed_ceiling: int, lines: int) -> tuple[Path, BudgetPolicy]:
    """Governed git repo whose HEAD^ carries a committed exemption floor.

    The draftSaveController 171→182 incident (#610): a file with a committed
    exemption that needed a higher ceiling had no legal path short of the
    release train. The exemption at ``committed_ceiling`` is committed into
    HEAD^; the caller then rewrites the registry in the working tree to play
    the re-file attempt. ``lines`` sizes the file so the refiled ceiling
    stays within the staleness band (ceiling ≤ actual + buffer).
    """
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=lines)
    write_baseline(root, {"server/app/example.py": 110})
    rewrite_exemption_ceiling(root, ceiling=committed_ceiling)
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    return root, replace(policy, growth_allowance=15)


def _check_with_refile(
    root: Path, policy: BudgetPolicy, ceiling: int, expires: str = ""
) -> list[str]:
    registry = root / "config/architecture/architecture-exemptions.yaml"
    entry = yaml.safe_load(registry.read_text())["exemptions"][0]
    entry["ceiling"] = ceiling
    if expires:
        entry["expires"] = expires
    registry.write_text(yaml.safe_dump({"exemptions": [entry]}), encoding="utf-8")
    return check_file_budgets(root, policy, load_exemptions(root))


class TestExemptionRefiling:
    """#641 re-file channel: within-band re-files pass, beyond-band need expires."""

    def test_within_band_refile_passes_without_expires(self, tmp_path: Path) -> None:
        root, policy = _refile_repo(tmp_path, committed_ceiling=171, lines=176)

        # The draftSaveController 171→182 raise: within floor + 15, no
        # ceremony needed — the release-train detour is gone.
        assert _check_with_refile(root, policy, 182) == []

    def test_beyond_band_refile_without_expires_errors(self, tmp_path: Path) -> None:
        root, policy = _refile_repo(tmp_path, committed_ceiling=171, lines=177)

        errors = _check_with_refile(root, policy, 187)

        assert errors == [
            "server/app/example.py: exemption ceiling 187 rose above committed "
            "ceiling 171 + growth allowance 15; beyond the allowance band a "
            "re-file must carry a future expires date (#641 time-boxed raise) "
            "or split the file"
        ]

    def test_beyond_band_refile_with_expires_passes(self, tmp_path: Path) -> None:
        root, policy = _refile_repo(tmp_path, committed_ceiling=171, lines=195)

        assert _check_with_refile(root, policy, 200, expires="2026-12-31") == []

    def test_zero_allowance_keeps_legacy_raise_rejected(self, tmp_path: Path) -> None:
        root, policy = _refile_repo(tmp_path, committed_ceiling=171, lines=166)
        strict = replace(policy, growth_allowance=0)

        errors = _check_with_refile(root, strict, 172)

        assert errors == [
            "server/app/example.py: exemption ceiling 172 rose above committed "
            "ceiling 171 + growth allowance 0; beyond the allowance band a "
            "re-file must carry a future expires date (#641 time-boxed raise) "
            "or split the file"
        ]

    def test_lowering_refile_still_passes(self, tmp_path: Path) -> None:
        root, policy = _refile_repo(tmp_path, committed_ceiling=171, lines=145)

        assert _check_with_refile(root, policy, 150) == []
