"""Static governance checks for tests/flaky_registry.yaml (Phase 5D)."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.check_reruns import ROOT_DIR, load_registry

pytestmark = pytest.mark.no_db

REGISTRY_PATH = ROOT_DIR / "tests" / "flaky_registry.yaml"


def test_registry_loads_with_valid_schema() -> None:
    entries = load_registry(REGISTRY_PATH)

    assert entries, "registry must not be empty"


def test_non_recurring_entries_have_unexpired_deadlines() -> None:
    today = date.today()
    expired = [
        f"{entry.entry_id} (deadline {entry.deadline})"
        for entry in load_registry(REGISTRY_PATH)
        if not entry.recurring and entry.deadline is not None and entry.deadline < today
    ]

    assert not expired, "fix the flake or extend the entry: " + ", ".join(expired)


def test_nodeid_entries_point_at_existing_test_files() -> None:
    missing = []
    for entry in load_registry(REGISTRY_PATH):
        if entry.nodeid is None:
            continue
        file_part = entry.nodeid.split("::", 1)[0]
        if not (ROOT_DIR / file_part).is_file():
            missing.append(f"{entry.entry_id}: {file_part}")

    assert not missing, "registry nodeids reference missing files: " + ", ".join(missing)
