"""Contract tests for direct smoke-tier file selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pytest_smoke_selection import smoke_test_files

pytestmark = pytest.mark.no_db


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "config" / "architecture").mkdir(parents=True)
    (tmp_path / "config" / "architecture" / "smoke-test-files.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": [
                    "tests/services/test_core.py",
                    "tests/full/test_architecture_full.py",
                ],
            }
        ),
        encoding="utf-8",
    )
    for relative in (
        "tests/services/test_core.py",
        "tests/scripts/test_architecture_contract.py",
        "tests/full/test_architecture_full.py",
        "tests/ci/test_architecture_ci.py",
        "tests/services/full/test_architecture_nested.py",
        "tests/services/ci/test_architecture_nested_ci.py",
        "tests/services/test_other.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def test_selection_matches_manifest_and_architecture_rule(tmp_path: Path) -> None:
    assert smoke_test_files(_repo(tmp_path)) == [
        "tests/scripts/test_architecture_contract.py",
        "tests/services/ci/test_architecture_nested_ci.py",
        "tests/services/full/test_architecture_nested.py",
        "tests/services/test_core.py",
    ]


def test_selection_rejects_missing_manifest_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tests" / "services" / "test_core.py").unlink()

    with pytest.raises(ValueError, match="missing files"):
        smoke_test_files(repo)


def test_current_smoke_selection_contains_every_manifest_file() -> None:
    selected = smoke_test_files()
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config/architecture/smoke-test-files.json"
        ).read_text(encoding="utf-8")
    )

    assert set(manifest["files"]) <= set(selected)
    assert any(path.startswith("tests/scripts/test_architecture_") for path in selected)
    assert not any(path.startswith(("tests/full/", "tests/ci/")) for path in selected)


@pytest.mark.parametrize(
    "entry", ["/tmp/test_escape.py", "tests/../test_escape.py", "other/test.py"]
)
def test_selection_rejects_paths_outside_tests(tmp_path: Path, entry: str) -> None:
    repo = _repo(tmp_path)
    (repo / "config" / "architecture" / "smoke-test-files.json").write_text(
        json.dumps({"version": 1, "files": [entry]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="escapes tests"):
        smoke_test_files(repo)


def test_selection_rejects_empty_final_set(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "config" / "architecture").mkdir(parents=True)
    (repo / "config" / "architecture" / "smoke-test-files.json").write_text(
        json.dumps({"version": 1, "files": ["tests/full/test_architecture_only.py"]}),
        encoding="utf-8",
    )
    path = repo / "tests" / "full" / "test_architecture_only.py"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        smoke_test_files(repo)
