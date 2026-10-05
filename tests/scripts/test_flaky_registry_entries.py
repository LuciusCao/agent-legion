"""Static governance checks for tests/flaky_registry.yaml (Phase 5D).

Clock-free by design (#941): this file runs in the unit tier on every PR, so
it validates schema and field legality only. Whether a deadline has passed is
a wall-clock judgement owned by the nightly ``check_reruns.py
--check-deadlines`` job; asserting it here turned every deadline day into a
red PR for unrelated diffs. The deadline *window* rule (≤ 45 days from the
entry's own ``registered_on`` / ``extended_on`` anchor) and nodeid uniqueness
are enforced by ``load_registry`` itself.
"""

from __future__ import annotations

import pytest

from scripts.check_reruns import MAX_DEADLINE_WINDOW_DAYS, ROOT_DIR, load_registry

pytestmark = pytest.mark.no_db

REGISTRY_PATH = ROOT_DIR / "tests" / "flaky_registry.yaml"


def test_registry_loads_with_valid_schema() -> None:
    entries = load_registry(REGISTRY_PATH)

    assert entries, "registry must not be empty"


def test_registry_nodeids_are_unique() -> None:
    nodeids = [entry.nodeid for entry in load_registry(REGISTRY_PATH) if entry.nodeid]

    assert len(nodeids) == len(set(nodeids))


def test_non_recurring_deadlines_stay_within_window() -> None:
    # Anchored on the entry's own dates, never on date.today(): the verdict
    # is identical on every day the suite runs.
    for entry in load_registry(REGISTRY_PATH):
        if entry.recurring:
            continue
        assert entry.deadline is not None and entry.registered_on is not None
        anchor = entry.extended_on or entry.registered_on
        assert 0 < (entry.deadline - anchor).days <= MAX_DEADLINE_WINDOW_DAYS, entry.entry_id


def test_nodeid_entries_point_at_existing_test_files() -> None:
    missing = []
    for entry in load_registry(REGISTRY_PATH):
        if entry.nodeid is None:
            continue
        file_part = entry.nodeid.split("::", 1)[0]
        if not (ROOT_DIR / file_part).is_file():
            missing.append(f"{entry.entry_id}: {file_part}")

    assert not missing, "registry nodeids reference missing files: " + ", ".join(missing)
