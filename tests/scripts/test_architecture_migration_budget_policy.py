"""Focused contracts for migration-root per-file ceiling exemption (#1030)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from scripts.architecture.budget_invariants import validate_budget_exemptions
from scripts.architecture.budget_inventory import build_budget_inventory
from scripts.architecture.budget_policy import load_budget_policy
from scripts.architecture.file_budgets import check_file_budgets
from scripts.quality.exemptions import ArchitectureExemption
from scripts.ratchet_architecture_budgets import ratchet_budgets
from tests.architecture_budget_helpers import git_repo, write_baseline

pytestmark = pytest.mark.no_db

MIGRATION_ROOT = "server/app/db/migrations"
MIGRATION_FILE = f"{MIGRATION_ROOT}/frozen.py"


def _policy_data(*, exempt_roots: object = None, max_lines: int = 800, allowance: int = 0) -> dict:
    production = {
        "roots": [{"path": "server/app", "extensions": [".py"]}],
        "exclude": [],
        "buffer_lines": 10,
        "max_lines": max_lines,
        "growth_allowance": allowance,
    }
    if exempt_roots is not None:
        production["per_file_ceiling_exempt_roots"] = exempt_roots
    return {
        "version": 1,
        "production": production,
        "tests": {
            "roots": [{"path": "tests", "patterns": ["**/*.py"]}],
            "max_lines": 1000,
        },
    }


def _write_policy_data(root: Path, data: dict) -> None:
    config = root / "config/architecture"
    config.mkdir(parents=True, exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _repo(
    tmp_path: Path,
    files: dict[str, int],
    baseline: dict[str, int],
    *,
    exempt_roots: object = (MIGRATION_ROOT,),
    max_lines: int = 800,
    allowance: int = 0,
) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    for path, lines in files.items():
        source = root / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("\n".join(f"x_{idx} = {idx}" for idx in range(lines)), encoding="utf-8")
    _write_policy_data(
        root,
        _policy_data(exempt_roots=exempt_roots, max_lines=max_lines, allowance=allowance),
    )
    write_baseline(root, baseline)
    return root


def _exemption(path: str) -> ArchitectureExemption:
    return ArchitectureExemption(
        check="architecture.file_budget",
        path=path,
        reason="Frozen migration needs a ceiling.",
        owner="architecture",
        remove_when="issues/open/001.md",
        ceiling=100,
    )


def test_policy_defaults_to_no_per_file_ceiling_exempt_roots(tmp_path: Path) -> None:
    _write_policy_data(tmp_path, _policy_data())

    policy = load_budget_policy(tmp_path / "config/architecture/architecture-budget-policy.yaml")

    assert policy.per_file_ceiling_exempt_roots == ()


def test_policy_loads_and_normalizes_exempt_roots(tmp_path: Path) -> None:
    _write_policy_data(
        tmp_path,
        _policy_data(exempt_roots=["server/app//db/migrations/"]),
    )

    policy = load_budget_policy(tmp_path / "config/architecture/architecture-budget-policy.yaml")

    assert policy.per_file_ceiling_exempt_roots == (MIGRATION_ROOT,)


@pytest.mark.parametrize(
    "value",
    [
        MIGRATION_ROOT,
        [1],
        [""],
        ["."],
        ["/absolute/path"],
        ["relative/../escape"],
        [MIGRATION_ROOT, "server/app//db/migrations"],
    ],
)
def test_policy_rejects_invalid_or_duplicate_exempt_roots(tmp_path: Path, value: object) -> None:
    _write_policy_data(tmp_path, _policy_data(exempt_roots=value))

    with pytest.raises(ValueError, match="per_file_ceiling_exempt_roots|duplicate"):
        load_budget_policy(tmp_path / "config/architecture/architecture-budget-policy.yaml")


def test_inventory_rejects_exempt_root_without_production_match(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"server/app/example.py": 10}, {"server/app/example.py": 20})
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    inventory, errors = build_budget_inventory(root, policy)

    assert inventory.per_file_ceiling_exempt == ()
    assert errors == [f"per-file ceiling exempt root matched no production file: {MIGRATION_ROOT}"]


def test_migration_bypasses_baseline_and_effective_ceiling(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {MIGRATION_FILE: 120, "server/app/example.py": 20},
        {MIGRATION_FILE: 1, "server/app/example.py": 30},
    )
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    assert check_file_budgets(root, policy, ()) == []


def test_migration_absolute_raw_limit_is_retained(tmp_path: Path) -> None:
    root = _repo(tmp_path, {MIGRATION_FILE: 51}, {}, max_lines=50)
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    assert check_file_budgets(root, policy, ()) == [
        f"{MIGRATION_FILE}: 51 lines exceeds absolute production limit 50; "
        "exemptions do not apply; split the file"
    ]


def test_migration_at_absolute_raw_limit_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path, {MIGRATION_FILE: 50}, {}, max_lines=50)
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    assert check_file_budgets(root, policy, ()) == []


def test_migration_sql_uses_global_raw_limit_not_db_schema_override(tmp_path: Path) -> None:
    migration_sql = f"{MIGRATION_ROOT}/frozen.sql"
    root = _repo(tmp_path, {migration_sql: 801}, {})
    policy_path = root / "config/architecture/architecture-budget-policy.yaml"
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    data["production"]["roots"].append(
        {"path": "server/app/db", "extensions": [".sql"], "max_lines": 1200}
    )
    policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    policy = load_budget_policy(policy_path)

    assert check_file_budgets(root, policy, ()) == [
        f"{migration_sql}: 801 lines exceeds absolute production limit 800; "
        "exemptions do not apply; split the file"
    ]


def test_exempt_root_does_not_match_sibling_directory(tmp_path: Path) -> None:
    sibling = "server/app/db/migrations_extra/current.py"
    root = _repo(tmp_path, {MIGRATION_FILE: 100, sibling: 20}, {})
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    assert check_file_budgets(root, policy, ()) == [
        f"{sibling}: production file has no baseline; run scripts/ratchet_architecture_budgets.py"
    ]


def test_non_migration_growth_allowance_is_unchanged(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {MIGRATION_FILE: 100, "server/app/example.py": 41},
        {MIGRATION_FILE: 1, "server/app/example.py": 25},
        allowance=15,
    )
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")

    assert check_file_budgets(root, policy, ()) == [
        "server/app/example.py: 41 effective lines exceeds ceiling 25 "
        "+ growth allowance 15; split the file, re-file the exemption, or revert growth"
    ]


def test_non_migration_monotonic_ceiling_is_unchanged(tmp_path: Path) -> None:
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    migration = root / MIGRATION_FILE
    migration.parent.mkdir(parents=True, exist_ok=True)
    migration.write_text("x = 1\n", encoding="utf-8")
    policy = replace(policy, per_file_ceiling_exempt_roots=(MIGRATION_ROOT,))
    write_baseline(root, {"server/app/example.py": 120, MIGRATION_FILE: 999})

    errors = check_file_budgets(root, policy, ())

    assert any(
        "server/app/example.py: ceiling 120 rose above committed ceiling 110" in error
        for error in errors
    )
    assert not any(MIGRATION_FILE in error and "rose above" in error for error in errors)


def test_file_budget_exemption_is_rejected_for_existing_and_future_migration_files(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path, {MIGRATION_FILE: 20}, {})
    policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")
    exemption = _exemption(f"{MIGRATION_ROOT}/future.py")
    expected = [
        f"{MIGRATION_ROOT}/future.py: architecture.file_budget exemptions are forbidden "
        f"under per-file ceiling exempt root {MIGRATION_ROOT}; remove the exemption"
    ]

    assert check_file_budgets(root, policy, (exemption,)) == expected
    assert validate_budget_exemptions(root, (exemption,)) == expected


def test_ratchet_removes_and_does_not_regenerate_migration_baselines(tmp_path: Path) -> None:
    new_migration = f"{MIGRATION_ROOT}/new.py"
    root = _repo(
        tmp_path,
        {MIGRATION_FILE: 120, new_migration: 5, "server/app/example.py": 40},
        {MIGRATION_FILE: 200, "server/app/example.py": 25},
        allowance=15,
    )

    first = ratchet_budgets(root)

    assert first.errors == ()
    assert first.changed is True
    baseline_path = root / "config/architecture/architecture-budgets.json"
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["files"] == {
        "server/app/example.py": 25
    }
    second = ratchet_budgets(root)
    assert second.errors == ()
    assert second.changed is False


def test_ratchet_rejects_unmatched_exempt_root_without_modifying_baseline(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path, {"server/app/example.py": 10}, {"server/app/example.py": 20})
    baseline_path = root / "config/architecture/architecture-budgets.json"
    before = baseline_path.read_text(encoding="utf-8")

    result = ratchet_budgets(root)

    assert result.changed is False
    assert result.errors == (
        f"per-file ceiling exempt root matched no production file: {MIGRATION_ROOT}",
    )
    assert baseline_path.read_text(encoding="utf-8") == before


def test_policy_mutation_removing_root_restores_missing_baseline_error(tmp_path: Path) -> None:
    root = _repo(tmp_path, {MIGRATION_FILE: 20}, {})
    policy_path = root / "config/architecture/architecture-budget-policy.yaml"
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    data["production"]["per_file_ceiling_exempt_roots"] = []
    policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    policy = load_budget_policy(policy_path)

    assert check_file_budgets(root, policy, ()) == [
        f"{MIGRATION_FILE}: production file has no baseline; "
        "run scripts/ratchet_architecture_budgets.py"
    ]
