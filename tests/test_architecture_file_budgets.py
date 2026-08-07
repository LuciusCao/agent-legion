"""Tests for scripts.architecture.file_budgets."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.architecture.budget_policy import BudgetPolicy, ProductionRoot, TestRoot
from scripts.architecture.file_budgets import (
    BudgetBaseline,
    check_file_budgets,
    count_source_lines,
    load_budget_baseline,
)
from scripts.quality.exemptions import ArchitectureExemption

pytestmark = pytest.mark.no_db


def governed_repo(tmp_path: Path, rel_path: str, *, lines: int) -> tuple[Path, BudgetPolicy]:
    root = tmp_path / "repo"
    file_path = root / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("\n".join(["line"] * lines), encoding="utf-8")

    config = root / "config" / "architecture"
    config.mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots:\n"
        "    - path: server/app\n"
        "      extensions: [.py]\n"
        "  exclude: []\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "tests:\n"
        "  roots:\n"
        "    - path: tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )

    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=(),
        buffer_lines=10,
        production_max_lines=1000,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=1000,
    )

    return root, policy


def write_baseline(root: Path, files_dict: dict[str, int]) -> None:
    path = root / "config" / "architecture" / "architecture-budgets.json"
    path.write_text(
        json.dumps({"version": 3, "files": files_dict}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def test_buffer_slack_allows_growth(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 110})
    assert check_file_budgets(root, policy, ()) == []


def test_rejects_growth_above_ceiling(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=111)
    write_baseline(root, {"server/app/example.py": 110})
    assert check_file_budgets(root, policy, ()) == [
        "server/app/example.py: 111 effective lines exceeds ceiling 110; "
        "split the file or revert growth"
    ]


def test_rejects_stale_ceiling_after_shrink(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=90)
    write_baseline(root, {"server/app/example.py": 110})
    assert check_file_budgets(root, policy, ()) == [
        "server/app/example.py: ceiling 110 is stale for 90 effective lines; "
        "run scripts/ratchet_architecture_budgets.py"
    ]


def test_comment_and_blank_lines_do_not_count_toward_ceiling(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    file_path = root / "server" / "app" / "example.py"
    content = file_path.read_text(encoding="utf-8")
    file_path.write_text(
        "# header comment\n\n" + content + "\n# trailing comment\n",
        encoding="utf-8",
    )
    write_baseline(root, {"server/app/example.py": 110})
    assert check_file_budgets(root, policy, ()) == []


def test_missing_production_baseline_entry_fails(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {})
    assert check_file_budgets(root, policy, ()) == [
        "server/app/example.py: production file has no baseline; "
        "run scripts/ratchet_architecture_budgets.py"
    ]


def test_stale_baseline_entry_for_excluded_file_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "server" / "app").mkdir(parents=True)
    (root / "server" / "app" / "generated.py").write_text(
        "\n".join(["line"] * 10), encoding="utf-8"
    )
    (root / "server" / "app" / "example.py").write_text("\n".join(["line"] * 10), encoding="utf-8")

    config = root / "config" / "architecture"
    config.mkdir(parents=True, exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots:\n"
        "    - path: server/app\n"
        "      extensions: [.py]\n"
        "  exclude:\n"
        "    - server/app/generated.py\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "tests:\n"
        "  roots:\n"
        "    - path: tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=("server/app/generated.py",),
        buffer_lines=10,
        production_max_lines=1000,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=1000,
    )
    write_baseline(root, {"server/app/example.py": 15, "server/app/generated.py": 15})

    assert check_file_budgets(root, policy, ()) == [
        "server/app/generated.py: stale baseline entry targets an excluded file; "
        "ratchet the baseline"
    ]


def test_stale_baseline_entry_for_non_production_file_fails(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105, "server/app/missing.py": 50})
    assert check_file_budgets(root, policy, ()) == [
        "server/app/missing.py: stale baseline entry targets a non-production file; "
        "ratchet the baseline"
    ]


@pytest.mark.parametrize(
    "baseline_text,expected_substring",
    [
        ("not json", "Malformed JSON"),
        ('{"version": 2, "files": {}}', "Unsupported baseline version"),
        ('{"version": 3, "files": {}, "extra": true}', "unknown fields"),
        ('{"files": {}}', "missing fields"),
        ('{"version": 3}', "missing fields"),
        ('{"version": 3, "files": {"a.py": true}}', "must be an integer"),
        ('{"version": 3, "files": {"a.py": 0}}', "must be positive"),
        ('{"version": 3, "files": {"a.py": -1}}', "must be positive"),
    ],
)
def test_bad_baseline_configuration_fails(
    tmp_path: Path, baseline_text: str, expected_substring: str
) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    path = root / "config" / "architecture" / "architecture-budgets.json"
    path.write_text(baseline_text, encoding="utf-8")
    errors = check_file_budgets(root, policy, ())
    assert len(errors) == 1
    assert errors[0].startswith("budget configuration:")
    assert expected_substring in errors[0]


def test_load_budget_baseline_rejects_boolean_ceiling(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"version": 3, "files": {"a.py": true}}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be an integer"):
        load_budget_baseline(path)


def test_load_budget_baseline_normalizes_paths(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"version": 3, "files": {"server/app//a.py": 10}}', encoding="utf-8")
    baseline = load_budget_baseline(path)
    assert baseline == BudgetBaseline(files={"server/app/a.py": 10})


def test_load_budget_baseline_rejects_normalized_path_collision(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        '{"version": 3, "files": {"server/app/a.py": 10, "server/app//a.py": 20}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate normalized baseline path"):
        load_budget_baseline(path)


def test_test_file_at_limit_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "server" / "app").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "big_test.py").write_text("\n".join(["line"] * 1000), encoding="utf-8")

    config = root / "config" / "architecture"
    config.mkdir(parents=True, exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots:\n"
        "    - path: server/app\n"
        "      extensions: [.py]\n"
        "  exclude: []\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "tests:\n"
        "  roots:\n"
        "    - path: tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=(),
        buffer_lines=10,
        production_max_lines=1000,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=1000,
    )
    write_baseline(root, {})

    assert check_file_budgets(root, policy, ()) == []


def test_test_file_over_limit_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "server" / "app").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "big_test.py").write_text("\n".join(["line"] * 1001), encoding="utf-8")

    config = root / "config" / "architecture"
    config.mkdir(parents=True, exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots:\n"
        "    - path: server/app\n"
        "      extensions: [.py]\n"
        "  exclude: []\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "tests:\n"
        "  roots:\n"
        "    - path: tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=(),
        buffer_lines=10,
        production_max_lines=1000,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=1000,
    )
    write_baseline(root, {})

    assert check_file_budgets(root, policy, ()) == [
        "tests/big_test.py: 1001 lines exceeds test limit 1000; split the test file"
    ]


def test_generated_code_excluded_not_required_in_baseline(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "server" / "app" / "generated").mkdir(parents=True)
    (root / "server" / "app" / "generated" / "api.py").write_text(
        "\n".join(["line"] * 200), encoding="utf-8"
    )
    (root / "server" / "app" / "example.py").write_text("\n".join(["line"] * 10), encoding="utf-8")

    config = root / "config" / "architecture"
    config.mkdir(parents=True, exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n"
        "  roots:\n"
        "    - path: server/app\n"
        "      extensions: [.py]\n"
        "  exclude:\n"
        "    - server/app/generated/**\n"
        "  buffer_lines: 10\n"
        "  max_lines: 800\n"
        "tests:\n"
        "  roots:\n"
        "    - path: tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    policy = BudgetPolicy(
        production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
        production_exclude=("server/app/generated/**",),
        buffer_lines=10,
        production_max_lines=1000,
        test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
        test_max_lines=1000,
    )
    write_baseline(root, {"server/app/example.py": 15})

    assert check_file_budgets(root, policy, ()) == []


def test_frozen_exemption_allows_maintaining_or_shrinking(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 50})
    exemption = ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when="issues/open/001.md",
        ceiling=100,
    )
    assert check_file_budgets(root, policy, (exemption,)) == []


def test_frozen_exemption_rejects_growth_above_exemption_ceiling(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=101)
    write_baseline(root, {"server/app/example.py": 50})
    exemption = ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when="issues/open/001.md",
        ceiling=100,
    )
    assert check_file_budgets(root, policy, (exemption,)) == [
        "server/app/example.py: 101 effective lines exceeds ceiling 100; "
        "split the file or revert growth"
    ]


def test_stale_exemption_fails_when_file_fits_normal_ceiling(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105})
    exemption = ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when="issues/open/001.md",
        ceiling=100,
    )
    assert check_file_budgets(root, policy, (exemption,)) == [
        "server/app/example.py: exemption ceiling 100 is stale; "
        "file fits within normal ceiling 105; "
        "remove the architecture.file_budget exemption"
    ]


def test_count_source_lines_counts_newlines(tmp_path: Path) -> None:
    path = tmp_path / "file.py"
    path.write_text("a\nb\nc", encoding="utf-8")
    assert count_source_lines(path) == 3


def test_absolute_production_limit_rejects_growth_even_with_exemption(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=60)
    policy = replace(policy, production_max_lines=50)
    write_baseline(root, {})
    exemption = ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when="issues/open/001.md",
        ceiling=60,
    )
    assert check_file_budgets(root, policy, (exemption,)) == [
        "server/app/example.py: 60 lines exceeds absolute production limit 50; "
        "exemptions do not apply; split the file"
    ]


def test_absolute_production_limit_rejects_growth_within_ceiling(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=60)
    policy = replace(policy, production_max_lines=50)
    write_baseline(root, {"server/app/example.py": 65})
    assert check_file_budgets(root, policy, ()) == [
        "server/app/example.py: 60 lines exceeds absolute production limit 50; "
        "exemptions do not apply; split the file"
    ]


def test_file_at_absolute_production_limit_passes(tmp_path: Path) -> None:
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=50)
    policy = replace(policy, production_max_lines=50)
    write_baseline(root, {"server/app/example.py": 55})
    assert check_file_budgets(root, policy, ()) == []
