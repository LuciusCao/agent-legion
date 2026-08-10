"""Tests for the tests/-root placement guard (scripts/architecture/test_placement.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.architecture.test_placement import check_test_placement, load_test_root_baseline
from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_baseline(root: Path, files: list[str]) -> None:
    write(
        root / "config/architecture/test-root-files-baseline.json",
        json.dumps({"version": 1, "files": files}, indent=2) + "\n",
    )


def test_repo_baseline_covers_current_tests_root() -> None:
    assert check_test_placement(REPO_ROOT) == []


def test_rejects_new_file_at_tests_root(tmp_path: Path) -> None:
    write(tmp_path / "tests/test_new_thing.py", "def test_x():\n    pass\n")
    write_baseline(tmp_path, ["tests/conftest.py"])

    errors = check_test_placement(tmp_path)

    assert len(errors) == 1
    assert "tests/test_new_thing.py" in errors[0]
    assert "AGENTS.md" in errors[0]


def test_accepts_baselined_root_file_and_subdirectory_tests(tmp_path: Path) -> None:
    write(tmp_path / "tests/conftest.py", "")
    write(tmp_path / "tests/test_legacy.py", "")
    write(tmp_path / "tests/services/test_new_thing.py", "")
    write_baseline(tmp_path, ["tests/conftest.py", "tests/test_legacy.py"])

    assert check_test_placement(tmp_path) == []


def test_ignores_non_python_files_at_tests_root(tmp_path: Path) -> None:
    write(tmp_path / "tests/flaky_registry.yaml", "entries: []\n")
    write_baseline(tmp_path, [])

    assert check_test_placement(tmp_path) == []


def test_missing_baseline_is_configuration_error(tmp_path: Path) -> None:
    errors = check_test_placement(tmp_path)

    assert any("test placement configuration" in error for error in errors)


def test_rejects_baseline_entries_outside_tests_root(tmp_path: Path) -> None:
    write(
        tmp_path / "config/architecture/test-root-files-baseline.json",
        '{"version": 1, "files": ["tests/services/test_x.py"]}',
    )

    with pytest.raises(ValueError, match="not a tests/ root file"):
        load_test_root_baseline(tmp_path / "config/architecture/test-root-files-baseline.json")


def test_check_repository_surfaces_new_tests_root_file(tmp_path: Path) -> None:
    write(tmp_path / "tests/test_stray.py", "def test_x():\n    pass\n")
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("tests/test_stray.py" in error and "AGENTS.md" in error for error in errors)
