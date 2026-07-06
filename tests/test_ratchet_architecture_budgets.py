from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.ratchet_architecture_budgets import main, ratchet_budgets


def _write_policy(root: Path, exclude: list[str] | None = None) -> None:
    policy = {
        "version": 1,
        "production": {
            "roots": [{"path": "server/app", "extensions": [".py"]}],
            "exclude": exclude if exclude is not None else [],
            "buffer_lines": 10,
        },
        "tests": {
            "roots": [{"path": "tests", "patterns": ["test_*.py"]}],
            "max_lines": 100,
        },
    }
    path = root / "config/architecture/architecture-budget-policy.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")


def _write_baseline(root: Path, files: dict[str, int]) -> None:
    path = root / "config/architecture/architecture-budgets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 2, "files": files}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_source_files(root: Path, files_dict: dict[str, int]) -> None:
    for rel_path, line_count in files_dict.items():
        file_path = root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            "\n".join(f"line {idx}" for idx in range(line_count)),
            encoding="utf-8",
        )


def _write_file_budget_exemption(root: Path, path: str, ceiling: int) -> None:
    exemption_path = root / "config/architecture/architecture-exemptions.yaml"
    exemption_path.write_text(
        yaml.safe_dump(
            {
                "exemptions": [
                    {
                        "check": "architecture.file_budget",
                        "path": path,
                        "ceiling": ceiling,
                        "reason": "Oversized module split is tracked.",
                        "owner": "architecture",
                        "remove_when": "issues/open/011-P2-testing-and-architecture-debt.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def configured_repo(
    tmp_path: Path,
    files_dict: dict[str, int],
    baseline: dict[str, int] | None = None,
    exclude: list[str] | None = None,
) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    _write_policy(root, exclude=exclude)
    _write_baseline(root, baseline if baseline is not None else {})
    _write_source_files(root, files_dict)
    return root


def read_baseline(root: Path) -> dict[str, int]:
    data = json.loads((root / "config/architecture/architecture-budgets.json").read_text())
    return data["files"]


def baseline_text(root: Path) -> str:
    return (root / "config/architecture/architecture-budgets.json").read_text(encoding="utf-8")


def test_adds_new_file_at_actual_plus_buffer(tmp_path):
    root = configured_repo(tmp_path, {"server/app/new.py": 20}, baseline={})
    result = ratchet_budgets(root)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/new.py": 30}


def test_refuses_to_raise_existing_ceiling(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 26},
        baseline={"server/app/example.py": 25},
    )
    before = baseline_text(root)
    result = ratchet_budgets(root)
    assert "exceeds ceiling 25" in result.errors[0]
    assert result.changed is False
    assert baseline_text(root) == before


def test_preserves_tighter_valid_ceiling_while_adding_new_file(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 100, "server/app/new.py": 5},
        baseline={"server/app/example.py": 103},
    )

    result = ratchet_budgets(root)

    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {
        "server/app/example.py": 103,
        "server/app/new.py": 15,
    }


def test_respects_frozen_exemption_while_adding_new_file(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/exempt.py": 100, "server/app/new.py": 5},
        baseline={"server/app/exempt.py": 50},
    )
    _write_file_budget_exemption(root, "server/app/exempt.py", 100)

    result = ratchet_budgets(root)

    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {
        "server/app/exempt.py": 50,
        "server/app/new.py": 15,
    }


def test_lowers_stale_ceiling(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 10},
        baseline={"server/app/example.py": 50},
    )
    result = ratchet_budgets(root)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/example.py": 20}


def test_deletes_obsolete_entries_for_removed_file(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/kept.py": 10},
        baseline={"server/app/kept.py": 15, "server/app/removed.py": 30},
    )
    result = ratchet_budgets(root)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/kept.py": 15}


def test_deletes_obsolete_entries_for_excluded_file(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/kept.py": 10, "server/app/excluded.py": 20},
        baseline={"server/app/kept.py": 15, "server/app/excluded.py": 25},
        exclude=["server/app/excluded.py"],
    )
    result = ratchet_budgets(root)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/kept.py": 15}


def test_output_is_sorted(tmp_path):
    root = configured_repo(
        tmp_path,
        {
            "server/app/zebra.py": 10,
            "server/app/alpha.py": 20,
            "server/app/middle.py": 15,
        },
        baseline={},
    )
    result = ratchet_budgets(root)
    assert result.errors == ()
    text = baseline_text(root)
    lines = text.splitlines()
    file_lines = [line for line in lines if '"server/app/' in line]
    keys = [line.split('"')[1] for line in file_lines]
    assert keys == sorted(keys)


def test_idempotent_second_run(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 10},
        baseline={"server/app/example.py": 15},
    )
    first = ratchet_budgets(root)
    assert first.errors == ()
    assert first.changed is False
    second = ratchet_budgets(root)
    assert second.errors == ()
    assert second.changed is False
    assert read_baseline(root) == {"server/app/example.py": 15}


def test_invalid_policy_does_not_modify_baseline(tmp_path):
    root = configured_repo(tmp_path, {"server/app/example.py": 10})
    (root / "config/architecture/architecture-budget-policy.yaml").write_text(
        "not: valid: yaml: [",
        encoding="utf-8",
    )
    before = baseline_text(root)
    result = ratchet_budgets(root)
    assert any("budget configuration" in error for error in result.errors)
    assert result.changed is False
    assert baseline_text(root) == before


def test_unmatched_exclusion_does_not_modify_baseline(tmp_path):
    root = configured_repo(tmp_path, {"server/app/example.py": 10})
    policy = {
        "version": 1,
        "production": {
            "roots": [{"path": "server/app", "extensions": [".py"]}],
            "exclude": ["server/app/does_not_exist.py"],
            "buffer_lines": 10,
        },
        "tests": {
            "roots": [{"path": "tests", "patterns": ["test_*.py"]}],
            "max_lines": 100,
        },
    }
    (root / "config/architecture/architecture-budget-policy.yaml").write_text(
        yaml.safe_dump(policy),
        encoding="utf-8",
    )
    before = baseline_text(root)
    result = ratchet_budgets(root)
    assert any("exclude glob matched no production file" in error for error in result.errors)
    assert result.changed is False
    assert baseline_text(root) == before


def test_cli_returns_zero_on_success(tmp_path):
    root = configured_repo(tmp_path, {"server/app/example.py": 10}, baseline={})
    assert main(["--root", str(root)]) == 0
    assert read_baseline(root) == {"server/app/example.py": 20}


def test_cli_returns_one_on_error(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 26},
        baseline={"server/app/example.py": 25},
    )
    assert main(["--root", str(root)]) == 1


def test_rebase_raises_existing_ceiling_to_desired(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 20},
        baseline={"server/app/example.py": 25},
    )
    result = ratchet_budgets(root, rebase=True)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/example.py": 30}


def test_rebase_is_idempotent(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 20},
        baseline={"server/app/example.py": 25},
    )
    first = ratchet_budgets(root, rebase=True)
    assert first.errors == ()
    assert first.changed is True
    assert read_baseline(root) == {"server/app/example.py": 30}

    second = ratchet_budgets(root, rebase=True)
    assert second.errors == ()
    assert second.changed is False
    assert read_baseline(root) == {"server/app/example.py": 30}


def test_rebase_still_respects_frozen_ceiling_when_actual_exceeds_it(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/exempt.py": 100},
        baseline={"server/app/exempt.py": 90},
    )
    _write_file_budget_exemption(root, "server/app/exempt.py", 90)
    result = ratchet_budgets(root, rebase=True)
    assert "exceeds ceiling 90" in result.errors[0]
    assert result.changed is False


def test_rebase_does_not_affect_frozen_exemption(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/exempt.py": 100, "server/app/new.py": 5},
        baseline={"server/app/exempt.py": 50},
    )
    _write_file_budget_exemption(root, "server/app/exempt.py", 100)

    result = ratchet_budgets(root, rebase=True)

    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {
        "server/app/exempt.py": 50,
        "server/app/new.py": 15,
    }


def test_cli_rebase_flag_passes_through(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 20},
        baseline={"server/app/example.py": 25},
    )
    assert main(["--root", str(root), "--rebase"]) == 0
    assert read_baseline(root) == {"server/app/example.py": 30}


def test_rebase_still_lowers_stale_ceiling(tmp_path):
    root = configured_repo(
        tmp_path,
        {"server/app/example.py": 10},
        baseline={"server/app/example.py": 50},
    )
    result = ratchet_budgets(root, rebase=True)
    assert result.errors == ()
    assert result.changed is True
    assert read_baseline(root) == {"server/app/example.py": 20}
