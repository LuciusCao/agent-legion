"""Tests for scripts.architecture.file_budgets."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

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
        _rewrite_exemption_ceiling(root, ceiling=exemption_ceiling)
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


def commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def write_exempted_repo(
    tmp_path: Path, ceiling: int, baseline_ceiling: int
) -> tuple[Path, BudgetPolicy, ArchitectureExemption]:
    """Governed git repo with a committed file_budget exemption at ``ceiling``."""
    root, policy = git_repo(tmp_path, files={"server/app/example.py": baseline_ceiling})
    registry = root / "config" / "architecture" / "architecture-exemptions.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
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
    commit_all(root, "file exemption")
    exemption = ArchitectureExemption(
        check="architecture.file_budget",
        path="server/app/example.py",
        reason="Oversized module needs staged split.",
        owner="agent-legion",
        remove_when="issues/open/001.md",
        ceiling=ceiling,
    )
    return root, policy, exemption


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


def test_rejects_uncommitted_raise_of_committed_ceiling(tmp_path: Path) -> None:
    # A hand-edited budgets.json raising a committed ceiling is caught by
    # comparing the working tree against HEAD.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    write_baseline(root, {"server/app/example.py": 120})
    errors = check_file_budgets(root, policy, ())
    assert any("ceiling 120 rose above committed ceiling 110" in error for error in errors)


def test_rejects_committed_raise_over_parent_commit(tmp_path: Path) -> None:
    # Smuggling the raise into a commit is caught against HEAD^ (HEAD's
    # first parent), so a committed raise fails the gate on the working tree.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    write_baseline(root, {"server/app/example.py": 120})
    commit_all(root, "raise ceiling by hand")
    errors = check_file_budgets(root, policy, ())
    assert any("rose above committed ceiling 110" in error for error in errors)


def test_accepts_committed_lowered_ceiling(tmp_path: Path) -> None:
    # Lowering is the sanctioned direction; both the working tree and a
    # committed lowering must stay green.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    write_baseline(root, {"server/app/example.py": 105})
    assert check_file_budgets(root, policy, ()) == []
    commit_all(root, "lower ceiling after split")
    assert check_file_budgets(root, policy, ()) == []


def test_accepts_new_entry_absent_from_committed_baseline(tmp_path: Path) -> None:
    # Registering a new file's ceiling (actual + buffer) is how ceilings
    # legitimately appear; only growth of an existing entry is a violation.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "new.py").write_text("\n".join(["line"] * 20), encoding="utf-8")
    write_baseline(root, {"server/app/example.py": 110, "server/app/new.py": 30})
    assert check_file_budgets(root, policy, ()) == []


def _rewrite_exemption_ceiling(root: Path, ceiling: int) -> None:
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


def test_rejects_exempt_ceiling_raise_over_committed_registry(tmp_path: Path) -> None:
    # The exemption channel may set a higher ceiling than the baseline entry,
    # but an exemption ceiling itself may not grow across revisions.
    root, policy, _exemption = write_exempted_repo(tmp_path, ceiling=120, baseline_ceiling=110)
    _rewrite_exemption_ceiling(root, ceiling=125)
    from scripts.architecture.exemptions import load_exemptions

    errors = check_file_budgets(root, policy, load_exemptions(root))
    assert any(
        "exemption ceiling 125 rose above committed ceiling 120" in error for error in errors
    )


def test_accepts_exempt_ceiling_lowered_over_committed_registry(tmp_path: Path) -> None:
    # 100 effective lines, buffer 10: the lowered exemption ceiling (105) is
    # within actual + buffer, and with baseline 95 the stale-exemption rule
    # (file must not fit the normal ceiling) no longer fires.
    root, policy, _exemption = write_exempted_repo(tmp_path, ceiling=130, baseline_ceiling=95)
    _rewrite_exemption_ceiling(root, ceiling=105)
    from scripts.architecture.exemptions import load_exemptions

    assert check_file_budgets(root, policy, load_exemptions(root)) == []


def test_accepts_first_time_exemption_without_prior_entry(tmp_path: Path) -> None:
    # Filing a dated file_budget exemption is the sanctioned raise channel
    # (#209): a first-time exemption must not trip the monotonic guard even
    # though its ceiling exceeds the committed baseline entry — the raise is
    # recorded in the registry with remove_when + age tracking instead.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 95})
    _rewrite_exemption_ceiling(root, ceiling=100)
    from scripts.architecture.exemptions import load_exemptions

    assert check_file_budgets(root, policy, load_exemptions(root)) == []


def test_accepts_exemption_retired_onto_lower_baseline(tmp_path: Path) -> None:
    # Retiring an exemption onto a baseline entry at or below the frozen
    # ceiling is a tightening (PR #226 pattern for routes/__init__.py): the
    # committed state carries the 105 exemption, the working tree replaces
    # it with a plain 103 baseline entry.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 95}, exemption_ceiling=105)
    (root / "config" / "architecture" / "architecture-exemptions.yaml").write_text(
        "exemptions: []\n", encoding="utf-8"
    )
    write_baseline(root, {"server/app/example.py": 103})
    assert check_file_budgets(root, policy, ()) == []


def test_rejects_exemption_retired_onto_higher_baseline(tmp_path: Path) -> None:
    # ...but retiring the exemption while simultaneously raising the baseline
    # entry above the frozen ceiling is a raise wearing a retirement costume.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 95}, exemption_ceiling=105)
    (root / "config" / "architecture" / "architecture-exemptions.yaml").write_text(
        "exemptions: []\n", encoding="utf-8"
    )
    write_baseline(root, {"server/app/example.py": 110})
    errors = check_file_budgets(root, policy, ())
    assert any("ceiling 110 rose above committed ceiling 105" in error for error in errors)


def test_monotonic_check_silent_without_git_history(tmp_path: Path) -> None:
    # Non-git checkouts (plain tmp_path) have no committed anchor; the check
    # must stay silent rather than fail. The ceiling stays within actual +
    # buffer so only the monotonic rule could complain.
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105})
    assert check_file_budgets(root, policy, ()) == []


def test_monotonic_check_fails_when_git_anchor_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A git checkout whose HEAD^ does not resolve (shallow clone, depth 1)
    # silently guts the committed-raise check exactly where CI gates PRs —
    # codex review on PR #231. An unresolvable anchor is a hard error unless
    # explicitly opted out. Built by hand: git_repo's fixture always carries
    # a resolvable HEAD^, so this test deliberately commits only once.
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105})
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    monkeypatch.delenv("AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW", raising=False)
    errors = check_file_budgets(root, policy, ())
    assert any("HEAD^" in error and "does not resolve" in error for error in errors)


def test_monotonic_check_shallow_opt_in_skips_anchor_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The explicit opt-out exists for depth-1 checkouts that genuinely
    # cannot fetch history; it skips the anchor errors but keeps the
    # against-HEAD comparison alive (uncommitted raises are still caught).
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 105})
    monkeypatch.setenv("AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW", "1")
    assert check_file_budgets(root, policy, ()) == []
    # ...and an uncommitted raise over the resolved HEAD anchor still fails.
    write_baseline(root, {"server/app/example.py": 130})
    errors = check_file_budgets(root, policy, ())
    assert any("rose above committed ceiling" in error for error in errors)


def test_working_tree_revert_of_committed_raise_passes(tmp_path: Path) -> None:
    # Documented semantics (review on PR #231): a working-tree fix reverting
    # an already-committed raise to the older, lower value must pass — the
    # floor is the minimum across anchors, so the pre-raise value wins.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    write_baseline(root, {"server/app/example.py": 120})
    commit_all(root, "raise ceiling by hand")
    write_baseline(root, {"server/app/example.py": 110})  # revert in working tree
    assert check_file_budgets(root, policy, ()) == []


def test_monotonic_check_fails_when_head_itself_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A git repo with zero commits (unborn HEAD) hits the same hard failure
    # for HEAD itself, not just HEAD^ — the opt-out covers both anchors.
    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105})
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    monkeypatch.delenv("AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW", raising=False)
    errors = check_file_budgets(root, policy, ())
    assert any("HEAD" in error and "does not resolve" in error for error in errors)


def test_rejects_ceiling_raise_via_rename_in_single_commit(tmp_path: Path) -> None:
    # The path-keyed floor treats a renamed file as a brand-new entry, so
    # "rename + grow + re-register" resets the ceiling — issue #236. The
    # renamed file carries its old floor forward: 140 lines re-registered at
    # 150 sits above the carried floor of 110 and must be rejected, both as
    # an uncommitted edit and smuggled into a commit.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    (root / "server" / "app" / "example2.py").write_text(
        "\n".join(["line"] * 140), encoding="utf-8"
    )
    write_baseline(root, {"server/app/example2.py": 150})
    errors = check_file_budgets(root, policy, ())
    assert any("example2.py" in error and "rename" in error for error in errors)
    # Same shape committed (the CI-visible smuggling path) is still rejected.
    commit_all(root, "rename + grow + re-register")
    errors = check_file_budgets(root, policy, ())
    assert any("example2.py" in error and "rename" in error for error in errors)


def test_rejects_rename_even_without_growth_after_ratchet_rebound(tmp_path: Path) -> None:
    # The friendly trigger path from issue #236: a file at actual ==
    # ceiling has no slack, so after a rename the plain budget check only
    # asks for a baseline entry. Registering at actual + buffer (the
    # ratchet-recommended value) re-inflates the buffer by rename — the old
    # floor (actual itself) must be carried forward and reject the +10.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 100})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    write_baseline(root, {"server/app/example2.py": 110})
    errors = check_file_budgets(root, policy, ())
    assert any("example2.py" in error and "rename" in error for error in errors)


def test_rename_to_equivalent_ceiling_passes(tmp_path: Path) -> None:
    # Renaming while keeping the ceiling at the old floor is a legitimate
    # move (pure rename, no growth): the carried floor equals the new
    # entry, nothing rose.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    write_baseline(root, {"server/app/example2.py": 110})
    assert check_file_budgets(root, policy, ()) == []


def test_rename_with_lowered_ceiling_passes(tmp_path: Path) -> None:
    # Rename plus a deliberate tightening (the sanctioned direction) stays
    # green — carrying the floor must not turn renames into a forced
    # ceiling freeze at exactly the old value.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    write_baseline(root, {"server/app/example2.py": 100})
    assert check_file_budgets(root, policy, ()) == []


def test_rename_with_shrunk_content_and_tighter_ceiling_passes(tmp_path: Path) -> None:
    # The sanctioned split-then-rename shape: content shrinks, ceiling drops
    # accordingly — a rename detection that fires here must still pass.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    (root / "server" / "app" / "example2.py").write_text("\n".join(["line"] * 50), encoding="utf-8")
    write_baseline(root, {"server/app/example2.py": 60})
    assert check_file_budgets(root, policy, ()) == []


def test_delete_old_file_and_create_unrelated_new_file_passes(tmp_path: Path) -> None:
    # Legitimate refactor: remove one file, add a different one. The new
    # file's content shares nothing with the deleted one, so no rename is
    # detected and the new entry registers at actual + buffer unrestricted.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").unlink()
    (root / "server" / "app" / "unrelated.py").write_text(
        "\n".join(["def completely_different_content() -> None:\n    pass"] * 40),
        encoding="utf-8",
    )
    write_baseline(root, {"server/app/unrelated.py": 90})
    assert check_file_budgets(root, policy, ()) == []


def test_rename_detection_requires_content_similarity_not_just_deletion(
    tmp_path: Path,
) -> None:
    # A deleted 100-line file and a new 100-line file of disjoint content
    # must not be paired by name or size heuristics — only git's own
    # similarity-based rename detection may carry a floor (#236 review
    # concern: normal delete+add refactors must not be misjudged).
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").unlink()
    (root / "server" / "app" / "other.py").write_text(
        "\n".join(f"alpha_{index} = {index}" for index in range(100)),
        encoding="utf-8",
    )
    write_baseline(root, {"server/app/other.py": 110})
    assert check_file_budgets(root, policy, ()) == []


def test_rejects_rename_floor_carry_when_detection_runs_committed(
    tmp_path: Path,
) -> None:
    # CI shape (issue #236 evidence): the rename lives inside HEAD — the
    # commit under review renamed the file — while HEAD^ still knows the old
    # path. The floor must be carried from HEAD^'s old-path entry even when
    # HEAD itself no longer contains it.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    (root / "server" / "app" / "example2.py").write_text(
        "\n".join(["line"] * 140), encoding="utf-8"
    )
    write_baseline(root, {"server/app/example2.py": 150})
    commit_all(root, "rename + grow + re-register")
    errors = check_file_budgets(root, policy, ())
    assert any("example2.py" in error and "rename" in error for error in errors)


def test_rejects_exemption_refiled_on_renamed_path_above_carried_floor(
    tmp_path: Path,
) -> None:
    # Exemption-channel variant of the rename bypass: file the dated
    # exemption on the renamed path above the old path's floor. The carried
    # floor bounds the exemption ceiling too — a rename is not a fresh
    # exemption filing.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server/app/example.py").rename(root / "server/app/example2.py")
    write_baseline(root, {"server/app/example2.py": 95})
    _rewrite_exemption_ceiling(root, ceiling=125)
    (root / "config" / "architecture" / "architecture-exemptions.yaml").write_text(
        (root / "config" / "architecture" / "architecture-exemptions.yaml")
        .read_text(encoding="utf-8")
        .replace("server/app/example.py", "server/app/example2.py"),
        encoding="utf-8",
    )
    from scripts.architecture.exemptions import load_exemptions

    errors = check_file_budgets(root, policy, load_exemptions(root))
    assert any(
        "exemption ceiling 125 rose above committed ceiling 110" in error for error in errors
    )


def test_accepts_exemption_on_renamed_path_within_carried_floor(
    tmp_path: Path,
) -> None:
    # The sanctioned use: rename and file an exemption at or below the old
    # floor — the carried floor constrains, it does not forbid.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    write_baseline(root, {"server/app/example2.py": 95})
    _rewrite_exemption_ceiling(root, ceiling=105)
    (root / "config" / "architecture" / "architecture-exemptions.yaml").write_text(
        (root / "config" / "architecture" / "architecture-exemptions.yaml")
        .read_text(encoding="utf-8")
        .replace("server/app/example.py", "server/app/example2.py"),
        encoding="utf-8",
    )
    from scripts.architecture.exemptions import load_exemptions

    assert check_file_budgets(root, policy, load_exemptions(root)) == []


def test_snapshot_failure_fails_closed_instead_of_missing_unstaged_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex review on PR #238: when untracked files exist (an unstaged
    # rename's new path is untracked) but the worktree snapshot index cannot
    # be built, falling back to a plain diff is fail-open — the plain diff
    # never sees the untracked target, the rename goes undetected, and the
    # new path passes as a first-time registration. The check must hard-fail
    # instead.
    root, policy = git_repo(tmp_path, files={"server/app/example.py": 110})
    (root / "server" / "app" / "example.py").rename(root / "server" / "app" / "example2.py")
    (root / "server" / "app" / "example2.py").write_text(
        "\n".join(["line"] * 140), encoding="utf-8"
    )
    write_baseline(root, {"server/app/example2.py": 150})

    def broken_snapshot(self: object) -> None:
        return None

    from scripts.architecture.budget_git import GitHelper

    monkeypatch.setattr(GitHelper, "_worktree_snapshot_index", broken_snapshot)
    monkeypatch.delenv("AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW", raising=False)
    errors = check_file_budgets(root, policy, ())
    assert any(
        "rename detection could not run" in error and "failing closed" in error for error in errors
    )


def test_monotonic_diagnostics_include_real_git_failure_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #236 follow-up: OSError/TimeoutExpired from the git helper used to be
    # swallowed into a synthesized stderr nobody read, so a missing git
    # binary or a timeout was reported as a plain shallow-clone suspicion.
    # The unresolvable-anchor error must carry the underlying reason.
    from scripts.architecture import budget_git

    root, policy = governed_repo(tmp_path, "server/app/example.py", lines=100)
    write_baseline(root, {"server/app/example.py": 105})

    def failing_run(*args: object, **kwargs: object) -> object:
        raise OSError("cannot spawn git binary")

    monkeypatch.delenv("AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW", raising=False)
    monkeypatch.setattr(budget_git.subprocess, "run", failing_run)
    errors = check_file_budgets(root, policy, ())
    assert any(
        "git failed to run" in error and "cannot spawn git binary" in error for error in errors
    )
