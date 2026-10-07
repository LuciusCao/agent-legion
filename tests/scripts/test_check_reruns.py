"""Contract tests for scripts/check_reruns.py (Phase 5D fail-on-rerun)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.check_reruns import (
    MAX_DEADLINE_WINDOW_DAYS,
    RegistryError,
    evaluate,
    expiring_soon,
    load_registry,
    load_rerun_nodeids,
    main,
    touched_entry_ids,
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
        "registered_on": "2026-08-02",
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
        {"registered_on": None},  # non-recurring without a window anchor
        {"registered_on": "not-a-date"},
        {"deadline": "2026-09-17"},  # 46 days after registered_on
        {"deadline": "2026-08-02"},  # not after the anchor
        {"extended_on": "2026-08-01"},  # extension precedes registration
        {"deadline": None, "recurring": True, "extended_on": "2026-08-10"},
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


def test_load_registry_rejects_duplicate_nodeids(tmp_path: Path) -> None:
    # #941: evaluate() keys entries by nodeid, so a second entry for the same
    # nodeid silently shadowed the first.
    registry = _write_registry(tmp_path / "registry.yaml", [_entry(), _entry(id="FLAKY-101")])

    with pytest.raises(RegistryError, match="duplicate nodeid"):
        load_registry(registry)


def test_deadline_window_is_anchored_on_entry_dates(tmp_path: Path) -> None:
    # The window rule is clock-free: the 45-day bound counts from the entry's
    # own registered_on / extended_on, so the verdict never depends on today.
    assert MAX_DEADLINE_WINDOW_DAYS == 45
    at_limit = _entry(deadline="2026-09-16")  # registered_on + 45
    extended = _entry(
        id="FLAKY-101",
        nodeid="tests/x/test_b.py::test_b",
        extended_on="2026-10-01",
        deadline="2026-11-15",  # extended_on + 45
    )

    entries = load_registry(_write_registry(tmp_path / "registry.yaml", [at_limit, extended]))

    assert [entry.deadline for entry in entries] == [date(2026, 9, 16), date(2026, 11, 15)]
    assert entries[1].extended_on == date(2026, 10, 1)

    too_late = _entry(extended_on="2026-10-01", deadline="2026-11-16")
    with pytest.raises(RegistryError, match="45-day window"):
        load_registry(_write_registry(tmp_path / "late.yaml", [too_late]))


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
        _write_registry(
            tmp_path / "registry.yaml", [_entry(deadline="2026-08-01", registered_on="2026-07-20")]
        )
    )

    _lines, violations = evaluate(entries, set(), TODAY, enforce_deadlines=True)

    assert any("FLAKY-100" in v and "expired" in v for v in violations)


def test_rerun_mode_reports_expired_deadlines_without_failing(tmp_path: Path) -> None:
    # #941: PR CI must not turn red because a calendar date passed; only the
    # nightly deadline-only mode enforces expiry.
    entries = load_registry(
        _write_registry(
            tmp_path / "registry.yaml", [_entry(deadline="2026-08-01", registered_on="2026-07-20")]
        )
    )

    lines, violations = evaluate(entries, {"tests/x/test_a.py::test_a"}, TODAY)

    assert violations == []
    assert any("FLAKY-100" in line and "expired" in line for line in lines)


def test_expiring_soon_lists_entries_within_seven_days(tmp_path: Path) -> None:
    entries = load_registry(
        _write_registry(
            tmp_path / "registry.yaml",
            [
                _entry(deadline="2026-08-10"),
                _entry(id="FLAKY-101", nodeid="tests/x/test_b.py::test_b", deadline="2026-08-11"),
                _entry(id="FLAKY-102", nodeid="tests/x/test_c.py::test_c", deadline="2026-08-03"),
            ],
        )
    )

    soon = expiring_soon(entries, TODAY)

    assert [entry.entry_id for entry in soon] == ["FLAKY-100", "FLAKY-102"]


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


def test_deadline_only_mode_requires_no_report(tmp_path: Path) -> None:
    # #295: nightly exemption-expiry runs the deadline check without any
    # rerun evidence — the mode must stand alone.
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])

    ok = main(["--registry", str(registry), "--check-deadlines", "--today", "2026-08-03"])
    assert ok == 0

    expired = main(["--registry", str(registry), "--check-deadlines", "--today", "2026-09-02"])
    assert expired == 1


def test_rerun_report_mode_passes_after_deadline(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])
    clean = _write_report(tmp_path / "clean.json", ["tests/x/test_a.py::test_a"])

    exit_code = main(
        ["--registry", str(registry), "--rerun-report", str(clean), "--today", "2026-09-02"]
    )

    assert exit_code == 0


def test_deadline_only_mode_warns_seven_days_ahead(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    quiet = main(["--registry", str(registry), "--check-deadlines", "--today", "2026-08-24"])
    assert quiet == 0
    assert "WARN" not in capsys.readouterr().out

    warned = main(["--registry", str(registry), "--check-deadlines", "--today", "2026-08-25"])
    out = capsys.readouterr().out
    assert warned == 0
    assert "WARN: FLAKY-100" in out
    assert "::warning title=flaky registry deadline::FLAKY-100" in out


def test_no_report_without_deadline_flag_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = _write_registry(tmp_path / "registry.yaml", [_entry()])

    with pytest.raises(SystemExit) as excinfo:
        main(["--registry", str(registry)])
    assert excinfo.value.code == 2
    assert "--check-deadlines" in capsys.readouterr().err


def test_base_registry_enforces_expiry_only_for_touched_entries(tmp_path: Path) -> None:
    # #941 R3: PRs into release/develop (never seen by the scheduled
    # deadline job) reject entries they add or re-date with an expired
    # deadline; untouched expired entries stay a note so no clock bomb.
    untouched = _entry(deadline="2026-08-01", registered_on="2026-07-20")
    redated = _entry(
        id="FLAKY-101",
        nodeid="tests/x/test_b.py::test_b",
        deadline="2026-08-02",
        registered_on="2026-07-20",
    )
    added = _entry(
        id="FLAKY-102",
        nodeid="tests/x/test_c.py::test_c",
        deadline="2026-08-01",
        registered_on="2026-07-20",
    )
    registry = _write_registry(tmp_path / "registry.yaml", [untouched, redated, added])
    # Old-schema base (no registered_on): parsed leniently; the backfilled
    # registered_on on FLAKY-100 is a schema migration, not a touch (#1034).
    base = _write_registry(
        tmp_path / "base.yaml",
        [
            {"id": "FLAKY-100", "nodeid": "tests/x/test_a.py::test_a", "deadline": "2026-08-01"},
            {"id": "FLAKY-101", "nodeid": "tests/x/test_b.py::test_b", "deadline": "2026-07-30"},
        ],
    )
    report = _write_report(tmp_path / "clean.json", [])

    entries = load_registry(registry)
    assert touched_entry_ids(entries, base) == {"FLAKY-101", "FLAKY-102"}

    args = ["--registry", str(registry), "--rerun-report", str(report), "--today", "2026-08-03"]
    assert main(args) == 0
    assert main([*args, "--base-registry", str(base)]) == 1

    fresh = _write_registry(tmp_path / "fresh.yaml", [untouched])
    assert (
        main(
            [
                "--registry",
                str(fresh),
                "--rerun-report",
                str(report),
                "--today",
                "2026-08-03",
                "--base-registry",
                str(base),
            ]
        )
        == 0
    )


def test_pr_gate_passes_base_registry_for_every_target_branch() -> None:
    # #941 R4: a default-branch condition let PRs into main add an already
    # expired entry; the base registry is passed whenever a base sha exists.
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/quality-gate.yml").read_text(
            encoding="utf-8"
        )
    )
    step = next(
        step
        for step in workflow["jobs"]["backend-coverage"]["steps"]
        if step.get("name") == "Enforce flaky rerun registry"
    )
    assert "pull_request.base.sha" in step["env"]["BASE_SHA"]
    assert "default_branch" not in str(step) and "base_ref" not in str(step)
    assert 'if [ -n "$BASE_SHA" ]; then' in step["run"]
    assert "--base-registry" in step["run"]
