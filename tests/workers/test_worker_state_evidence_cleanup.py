from __future__ import annotations

import os
from pathlib import Path

import pytest

from worker.state_evidence_cleanup import RAW_EVENTS_FILENAME, sweep_raw_event_copies
from worker.upload.constants import PENDING_FILENAME

pytestmark = pytest.mark.no_db

_NOW = 2_000_000.0


def _raw(root: Path, execution_id: str, *, age: float) -> Path:
    incident = root / f"{execution_id}__node-a"
    incident.mkdir(parents=True)
    raw = incident / RAW_EVENTS_FILENAME
    raw.write_bytes(b"unredacted staging")
    mtime = _NOW - age
    os.utime(raw, (mtime, mtime))
    return raw


def test_sweep_removes_only_abandoned_raw_and_preserves_recovery_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    raw = _raw(root, "exec-old", age=100)
    incident = raw.parent
    for name in ("events.jsonl", "events-emergency.jsonl", "incident.json"):
        (incident / name).write_text("processed", encoding="utf-8")
    recovered = incident / "recovered" / "events.jsonl"
    recovered.parent.mkdir()
    recovered.write_text("lease-scoped recovery", encoding="utf-8")
    foreign = root / "foreign" / RAW_EVENTS_FILENAME
    foreign.parent.mkdir()
    foreign.write_bytes(b"keep")
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    (incident / "linked.raw").symlink_to(outside)

    assert sweep_raw_event_copies(root, tmp_path / "work", now=_NOW, min_age_seconds=50) == 1

    assert not raw.exists()
    assert (incident / "events.jsonl").is_file()
    assert (incident / "events-emergency.jsonl").is_file()
    assert (incident / "incident.json").is_file()
    assert recovered.is_file()
    assert foreign.is_file()
    assert (incident / "linked.raw").is_symlink()
    assert outside.is_file()


def test_sweep_age_and_pending_ownership_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    work = tmp_path / "work"
    fresh = _raw(root, "exec-fresh", age=5)
    owned = _raw(root, "exec-owned", age=100)
    other = _raw(root, "exec-other", age=100)
    marker = work / "exec-owned" / PENDING_FILENAME
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    assert sweep_raw_event_copies(root, work, now=_NOW, min_age_seconds=50) == 1
    assert fresh.is_file()
    assert owned.is_file()
    assert not other.exists()

    marker.unlink()
    assert sweep_raw_event_copies(root, work, now=_NOW, min_age_seconds=50) == 1
    assert not owned.exists()
    assert fresh.is_file()


def test_sweep_exact_age_boundary(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    younger = _raw(root, "exec-younger", age=49)
    at_limit = _raw(root, "exec-at-limit", age=50)
    older = _raw(root, "exec-older", age=51)

    assert sweep_raw_event_copies(root, tmp_path / "work", now=_NOW, min_age_seconds=50) == 2
    assert younger.is_file()
    assert not at_limit.exists()
    assert not older.exists()


def test_sweep_scan_work_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    raws = [_raw(root, f"exec-{index}", age=100) for index in range(3)]

    assert (
        sweep_raw_event_copies(root, tmp_path / "work", now=_NOW, min_age_seconds=50, scan_limit=2)
        == 2
    )
    assert sum(path.exists() for path in raws) == 1


def test_sweep_ignores_raw_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    incident = root / "exec-old__node-a"
    incident.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"evidence")
    raw = incident / RAW_EVENTS_FILENAME
    raw.symlink_to(outside)

    assert sweep_raw_event_copies(root, tmp_path / "work", now=_NOW, min_age_seconds=0) == 0
    assert raw.is_symlink()
    assert outside.is_file()
