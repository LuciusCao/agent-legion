"""Contract tests for scripts/check_reruns.py (Phase 5D fail-on-rerun)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.check_reruns import (
    RegistryError,
    evaluate,
    load_registry,
    load_rerun_nodeids,
    main,
)

pytestmark = pytest.mark.no_db

TODAY = date(2026, 8, 3)


def _write_registry(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


def _entry(**overrides: object) -> dict:
    base: dict[str, object] = {
        "id": "FLAKY-100",
        "nodeid": "tests/x/test_a.py::test_a",
        "owner": "test-infra",
        "reason": "known flake",
        "observed": "local run 2026-08-01",
        "deadline": "2026-09-01",
    }
    base.update(overrides)
    return base


def _write_report(path: Path, tests: list[str], attempts: int | None = None) -> Path:
    payload = {
        "attempts": attempts if attempts is not None else len(tests),
        "exitstatus": 0,
        "tests": tests,
        "reports": [{"nodeid": t, "phase": "call"} for t in tests],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_registry_accepts_valid_entries(tmp_path: Path) -> None:
    registry = _write_registry(
        tmp_path / "registry.yaml",
        [
            _entry(),
            _entry(
                id="FLAKY-101",
                nodeid=None,
                scope="ci-infra:docker-hub",
                deadline=None,
                recurring=True,
            ),
        ],
    )

    entries = load_registry(registry)

    assert len(entries) == 2
    assert entries[0].nodeid == "tests/x/test_a.py::test_a"
    assert entries[0].deadline == date(2026, 9, 1)
    assert entries[1].recurring is True
    assert entries[1].deadline is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"deadline": None},  # non-recurring without deadline
        {"recurring": True},  # recurring with deadline still set
        {"scope": "frontend:x"},  # both nodeid and scope
        {"nodeid": None, "scope": None},  # neither nodeid nor scope
        {"owner": ""},
    ],
)
def test_load_registry_rejects_invalid_entries(tmp_path: Path, overrides: dict) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry(**overrides)])

    with pytest.raises(RegistryError):
        load_registry(registry)


def test_load_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry(), _entry()])

    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(registry)


def test_load_rerun_nodeids_skips_missing_reports(tmp_path: Path) -> None:
    report = _write_report(tmp_path / "reruns.json", ["tests/x/test_a.py::test_a"])

    nodeids, missing = load_rerun_nodeids([report, tmp_path / "absent.json"])

    assert nodeids == {"tests/x/test_a.py::test_a"}
    assert missing == [tmp_path / "absent.json"]


def test_evaluate_flags_unregistered_reruns(tmp_path: Path) -> None:
    entries = load_registry(_write_registry(tmp_path / "registry.yaml", [_entry()]))

    lines, violations = evaluate(
        entries,
        {"tests/x/test_a.py::test_a", "tests/y/test_b.py::test_b"},
        TODAY,
    )

    assert any("rerun outside registry: tests/y/test_b.py::test_b" in v for v in violations)
    assert not any("test_a" in v for v in violations)
    assert any("registered: tests/x/test_a.py::test_a" in line for line in lines)


def test_evaluate_flags_expired_deadlines(tmp_path: Path) -> None:
    entries = load_registry(
        _write_registry(tmp_path / "registry.yaml", [_entry(deadline="2026-08-01")])
    )

    _lines, violations = evaluate(entries, set(), TODAY)

    assert any("FLAKY-100" in v and "expired" in v for v in violations)


def test_evaluate_recurring_entries_never_expire(tmp_path: Path) -> None:
    entries = load_registry(
        _write_registry(
            tmp_path / "registry.yaml",
            [
                _entry(
                    id="FLAKY-102",
                    nodeid=None,
                    scope="ci-infra:x",
                    deadline=None,
                    recurring=True,
                )
            ],
        )
    )

    _lines, violations = evaluate(entries, set(), date(2099, 1, 1))

    assert violations == []


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])
    clean = _write_report(tmp_path / "clean.json", ["tests/x/test_a.py::test_a"])
    dirty = _write_report(tmp_path / "dirty.json", ["tests/y/test_b.py::test_b"])

    ok = main(
        [
            "--registry",
            str(registry),
            "--rerun-report",
            str(clean),
            "--today",
            "2026-08-03",
        ]
    )
    assert ok == 0
    assert "OK" in capsys.readouterr().out

    bad = main(
        [
            "--registry",
            str(registry),
            "--rerun-report",
            str(dirty),
            "--today",
            "2026-08-03",
        ]
    )
    assert bad == 1
    assert "rerun outside registry" in capsys.readouterr().out


def test_main_tolerates_missing_reports(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--rerun-report",
            str(tmp_path / "absent.json"),
            "--today",
            "2026-08-03",
        ]
    )

    assert exit_code == 0
