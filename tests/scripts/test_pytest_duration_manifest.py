"""Tests for deterministic JUnit-to-file duration manifest generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pytest_duration_manifest import build_manifest

pytestmark = pytest.mark.no_db


def _repo(tmp_path: Path) -> Path:
    for name in (
        "test_alpha.py",
        "test_beta.py",
        "test_extra.py",
        "test_zero.py",
        "native_sandbox_probe_contract_test.py",
    ):
        path = tmp_path / "tests" / "pkg" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def _junit(path: Path, cases: list[tuple[str, str, str]]) -> Path:
    body = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{seconds}" />'
        for classname, name, seconds in cases
    )
    path.write_text(f"<testsuite>{body}</testsuite>", encoding="utf-8")
    return path


def test_manifest_aggregates_total_durations_by_source_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _junit(
        tmp_path / "a.xml",
        [
            ("tests.pkg.test_alpha.TestAlpha", "test_one", "1.25"),
            ("tests.pkg.test_alpha.TestAlpha", "test_two", "2.0"),
            ("tests.pkg.test_beta", "test_three", "4.5"),
        ],
    )
    second = _junit(
        tmp_path / "b.xml",
        [("tests.pkg.test_extra", "test_four[param]", "3.0")],
    )

    manifest = build_manifest([first, second], repo_root=repo, source_run="12345")

    assert manifest == {
        "version": 1,
        "source_run": "12345",
        "files": {
            "tests/pkg/test_alpha.py": {"seconds": 3.25, "tests": 2},
            "tests/pkg/test_beta.py": {"seconds": 4.5, "tests": 1},
            "tests/pkg/test_extra.py": {"seconds": 3.0, "tests": 1},
        },
    }
    assert json.loads(json.dumps(manifest)) == manifest


def test_manifest_rejects_duplicate_testcases_across_shards(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case = [("tests.pkg.test_alpha", "test_same", "1.0")]
    first = _junit(tmp_path / "a.xml", case)
    second = _junit(tmp_path / "b.xml", case)

    with pytest.raises(ValueError, match="duplicate JUnit testcase"):
        build_manifest([first, second], repo_root=repo, source_run="123")


def test_manifest_rejects_unmapped_classname(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = _junit(tmp_path / "a.xml", [("tests.pkg.test_missing", "test_one", "1.0")])

    with pytest.raises(ValueError, match="cannot map JUnit classname"):
        build_manifest([report], repo_root=repo, source_run="123")


def test_manifest_rejects_non_finite_duration(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = _junit(tmp_path / "a.xml", [("tests.pkg.test_alpha", "test_one", "nan")])

    with pytest.raises(ValueError, match="invalid JUnit duration"):
        build_manifest([report], repo_root=repo, source_run="123")


def test_manifest_accepts_zero_duration_and_pytest_suffix_patterns(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = _junit(
        tmp_path / "a.xml",
        [
            ("tests.pkg.test_zero", "test_zero", "0.0"),
            ("tests.pkg.native_sandbox_probe_contract_test", "test_contract", "1.5"),
        ],
    )

    manifest = build_manifest([report], repo_root=repo, source_run="123")

    assert manifest["files"] == {
        "tests/pkg/native_sandbox_probe_contract_test.py": {"seconds": 1.5, "tests": 1},
        "tests/pkg/test_zero.py": {"seconds": 0.0, "tests": 1},
    }
