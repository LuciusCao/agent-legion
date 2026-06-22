"""Tests for scripts.architecture.budget_inventory."""

from __future__ import annotations

from pathlib import Path

from scripts.architecture.budget_inventory import BudgetInventory, build_budget_inventory
from scripts.architecture.budget_policy import BudgetPolicy, ProductionRoot, TestRoot


def write_files(root: Path, *paths: str) -> None:
    for path in paths:
        full_path = root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("", encoding="utf-8")


def complete_policy() -> BudgetPolicy:
    return BudgetPolicy(
        production_roots=(
            ProductionRoot(path="server/app", extensions=(".py",)),
            ProductionRoot(path="frontend/src", extensions=(".tsx", ".ts", ".css")),
            ProductionRoot(path="scripts", extensions=(".py",)),
        ),
        production_exclude=("frontend/src/generated/**",),
        buffer_lines=100,
        test_roots=(
            TestRoot(path="tests", patterns=("**/*.py",)),
            TestRoot(path="frontend/src", patterns=("**/*.test.tsx", "**/*.test.ts")),
        ),
        test_max_lines=500,
    )


def test_classifies_tests_first_and_excludes_generated(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        "server/app/main.py",
        "frontend/src/App.tsx",
        "frontend/src/styles.css",
        "frontend/src/App.test.tsx",
        "frontend/src/generated/api.ts",
        "scripts/check.py",
        "tests/test_main.py",
    )
    inventory, errors = build_budget_inventory(tmp_path, complete_policy())
    assert errors == []
    assert inventory.production == (
        "frontend/src/App.tsx",
        "frontend/src/styles.css",
        "scripts/check.py",
        "server/app/main.py",
    )
    assert inventory.tests == ("frontend/src/App.test.tsx", "tests/test_main.py")
    assert inventory.excluded == ("frontend/src/generated/api.ts",)


def test_test_first_beats_production_extension(tmp_path: Path) -> None:
    write_files(tmp_path, "frontend/src/App.test.tsx")
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="frontend/src", extensions=(".tsx",)),),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(TestRoot(path="frontend/src", patterns=("**/*.test.tsx",)),),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert errors == []
    assert inventory.tests == ("frontend/src/App.test.tsx",)
    assert inventory.production == ()
    assert inventory.excluded == ()


def test_root_level_and_nested_test_patterns(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        "frontend/src/App.test.tsx",
        "frontend/src/components/Button.test.tsx",
        "frontend/src/deep/nested/Item.test.ts",
    )
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="frontend/src", extensions=(".tsx", ".ts")),),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(TestRoot(path="frontend/src", patterns=("**/*.test.tsx", "**/*.test.ts")),),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert errors == []
    assert inventory.tests == (
        "frontend/src/App.test.tsx",
        "frontend/src/components/Button.test.tsx",
        "frontend/src/deep/nested/Item.test.ts",
    )


def test_duplicate_production_roots_reported(tmp_path: Path) -> None:
    write_files(tmp_path, "server/app/main.py")
    policy = BudgetPolicy(
        production_roots=(
            ProductionRoot(path="server/app", extensions=(".py",)),
            ProductionRoot(path="server", extensions=(".py",)),
        ),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert inventory.production == ()
    assert any("duplicate classification" in e and "server/app/main.py" in e for e in errors)


def test_missing_root_directory_is_graceful(tmp_path: Path) -> None:
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="does/not/exist", extensions=(".py",)),),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(TestRoot(path="also/missing", patterns=("**/*.py",)),),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert errors == []
    assert inventory == BudgetInventory(production=(), tests=(), excluded=())


def test_exclusion_glob_matching_nothing_reported(tmp_path: Path) -> None:
    write_files(tmp_path, "server/app/main.py")
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=("server/app/missing/**",),
        buffer_lines=10,
        test_roots=(),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert inventory.production == ("server/app/main.py",)
    assert errors == ["exclude glob matched no production file: server/app/missing/**"]


def test_output_order_is_stable(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        "server/app/zebra.py",
        "server/app/aardvark.py",
        "frontend/src/Z.tsx",
        "frontend/src/A.tsx",
        "tests/z_test.py",
        "tests/a_test.py",
    )
    policy = BudgetPolicy(
        production_roots=(
            ProductionRoot(path="server/app", extensions=(".py",)),
            ProductionRoot(path="frontend/src", extensions=(".tsx",)),
        ),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert errors == []
    assert inventory.production == (
        "frontend/src/A.tsx",
        "frontend/src/Z.tsx",
        "server/app/aardvark.py",
        "server/app/zebra.py",
    )
    assert inventory.tests == ("tests/a_test.py", "tests/z_test.py")


def test_generated_under_frontend_excluded_by_policy(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        "frontend/src/generated/api.ts",
        "frontend/src/generated/helpers.ts",
        "frontend/src/App.tsx",
    )
    inventory, errors = build_budget_inventory(tmp_path, complete_policy())
    assert errors == []
    assert inventory.production == ("frontend/src/App.tsx",)
    assert inventory.excluded == (
        "frontend/src/generated/api.ts",
        "frontend/src/generated/helpers.ts",
    )


def test_directory_symlink_not_followed(tmp_path: Path) -> None:
    real_dir = tmp_path / "server" / "app"
    real_dir.mkdir(parents=True)
    (real_dir / "main.py").write_text("", encoding="utf-8")
    link_dir = tmp_path / "frontend" / "src" / "linked"
    link_dir.parent.mkdir(parents=True)
    link_dir.symlink_to(real_dir, target_is_directory=True)
    policy = BudgetPolicy(
        production_roots=(
            ProductionRoot(path="server/app", extensions=(".py",)),
            ProductionRoot(path="frontend/src", extensions=(".py",)),
        ),
        production_exclude=(),
        buffer_lines=10,
        test_roots=(),
        test_max_lines=100,
    )
    inventory, errors = build_budget_inventory(tmp_path, policy)
    assert errors == []
    assert inventory.production == ("server/app/main.py",)
