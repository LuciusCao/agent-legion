"""Shared scaffolding for the architecture-budget / boundary test suites.

Test modules must not import each other (guarded by
``tests/app/test_pytest_postgres_boundaries.py``): fixtures shared across
suite files live here. The git fixtures build real repositories because the
monotonic ceiling/boundary guards compare against committed revisions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from scripts.architecture.budget_policy import BudgetPolicy, ProductionRoot, TestRoot


def write_neutral_budget_governance(root: Path) -> None:
    config = root / "config/architecture"
    config.mkdir(parents=True, exist_ok=True)
    (root / "budget-fixtures").mkdir(exist_ok=True)
    (root / "budget-tests").mkdir(exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n  roots:\n"
        "    - path: budget-fixtures\n"
        "      extensions: [.py]\n"
        "  exclude: []\n"
        "  buffer_lines: 5\n"
        "  max_lines: 800\n"
        "tests:\n  roots:\n"
        "    - path: budget-tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    (config / "architecture-budgets.json").write_text(
        '{\n  "version": 3, "files": {}\n}\n', encoding="utf-8"
    )
    (config / "sql-placeholders-baseline.json").write_text(
        '{\n  "version": 1, "files": {}\n}\n', encoding="utf-8"
    )
    (config / "test-root-files-baseline.json").write_text(
        '{\n  "version": 1, "files": []\n}\n', encoding="utf-8"
    )
    (config / "service-data-boundary-baseline.json").write_text(
        '{\n  "version": 1, "files": {}\n}\n', encoding="utf-8"
    )
    (config / "docs-retired-terms.yaml").write_text(
        "terms:\n  - pattern: '\\bopenclaw\\b'\n    retired_in: '#75'\nexemptions: []\n",
        encoding="utf-8",
    )


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


def rewrite_exemption_ceiling(root: Path, ceiling: int) -> None:
    registry = root / "config" / "architecture" / "architecture-exemptions.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "exemptions": [
                    {
                        "check": "architecture.file_budget",
                        "path": "server/app/example.py",
                        "ceiling": ceiling,
                        "reason": "Oversized module needs staged split.",
                        "owner": "agent-legion",
                        "remove_when": "issues/open/001.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def git_repo(
    tmp_path: Path,
    files: dict[str, int] | None = None,
    exemption_ceiling: int | None = None,
) -> tuple[Path, BudgetPolicy]:
    """Build a governed repo inside a real git repository.

    The monotonic ceiling check compares against committed revisions, so its
    tests need an actual git history; ``init`` + ``commit`` of the initial
    state provides the HEAD anchor, and a second commit (or an uncommitted
    working-tree edit) plays the role of the raise attempt. The leading
    empty commit keeps HEAD^ resolvable in every fixture — an unresolvable
    anchor is itself an error since the codex review on PR #231 (shallow
    clones must not silently gut the check), and only the dedicated
    unresolvable-anchor tests build that shape deliberately.
    """
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, files if files is not None else {"server/app/example.py": 110})
    if exemption_ceiling is not None:
        rewrite_exemption_ceiling(root, ceiling=exemption_ceiling)
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        # Empty seed commit: HEAD^ must resolve in the fixture repos.
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    return root, policy


def write_boundary_baseline(root: Path, files: dict[str, list[int]]) -> None:
    baseline_path = root / "config/architecture/service-data-boundary-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps({"version": 1, "files": files}, indent=2) + "\n", encoding="utf-8"
    )


def boundary_git_repo(tmp_path: Path, entries: dict[str, list[int]]) -> Path:
    """Build a real git repo whose HEAD^ carries a boundary baseline.

    An empty seed commit keeps HEAD^ resolvable (mirrors the budget
    monotonicity fixtures); the baseline JSON (and its service files) commit
    into HEAD^ — the pre-change anchor — so the working-tree edit plays the
    raise attempt against genuine history.
    """
    root = tmp_path
    write_boundary_baseline(root, entries)
    for path in entries:
        service = root / path
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text('A = "SELECT 1"\n', encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init baseline"],
        # A trailing commit so the baseline lands in HEAD^ (the pre-change
        # anchor), leaving HEAD as the working tree's comparison point.
        ["git", "commit", "-q", "--allow-empty", "-m", "trailing"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    return root
