"""Lenient reads of OTHER revisions' flaky registries (#941, #1024, #1034).

``flaky_registry.load_registry`` validates the working tree strictly. A
registry from another revision — a PR's target branch, or a maintained
release branch read by the nightly job — may predate the current schema
(release branches cut before #941 have no ``registered_on``), so it is read
here field by field: only the entry ``id`` is required, everything else is
taken as found. Split out of ``flaky_registry.py`` for its file budget.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.quality.flaky_registry import RegistryEntry, RegistryError

# Fields that decide WHICH test an exemption absorbs and WHEN it lapses
# (#1034): changing any of them re-opens the entry for the PR's expiry check.
_TARGET_FIELDS = ("nodeid", "scope", "recurring", "deadline")
# The #941 anchor dates; compared only when the base already carries
# ``registered_on`` — an old-schema base lacks both, and a backfilled date
# there is a schema migration, not a re-dated exemption.
_ANCHOR_FIELDS = ("registered_on", "extended_on")


def lenient_entries(text: str, source: str) -> dict[str, dict]:
    """Raw entries keyed by stripped ``id``; entries without an id are skipped."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"cannot parse registry {source}: {exc}") from exc
    raw_entries = data.get("entries") if isinstance(data, dict) else None
    entries: dict[str, dict] = {}
    for raw in raw_entries if isinstance(raw_entries, list) else []:
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            entries[raw["id"].strip()] = raw
    return entries


def _normalized(raw: dict, field: str) -> str:
    value = raw.get(field)
    if field == "recurring":
        return str(bool(value))
    return str(value.strip() if isinstance(value, str) else value)


def _fingerprint(raw: dict, *, with_anchors: bool) -> tuple[str, ...]:
    fields = _TARGET_FIELDS + (_ANCHOR_FIELDS if with_anchors else ())
    return tuple(_normalized(raw, field) for field in fields)


def _entry_as_raw(entry: RegistryEntry) -> dict:
    return {
        "nodeid": entry.nodeid,
        "scope": entry.scope,
        "recurring": entry.recurring,
        "deadline": entry.deadline,
        "registered_on": entry.registered_on,
        "extended_on": entry.extended_on,
    }


def touched_entry_ids(entries: list[RegistryEntry], base_path: Path) -> set[str]:
    """Ids of entries added or re-targeted relative to a base registry.

    Every PR enforces expiry for these (#941 R3/R4: the nightly job sees
    only the default branch, and only after a merge), yet untouched entries
    must not turn every PR red on their deadline day. "Touched" covers every
    field that decides what the exemption absorbs and until when (#1034):
    keeping an expired entry's id and deadline while pointing it at a new
    nodeid / scope must not let that new test's reruns be absorbed.
    """
    try:
        text = base_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read base registry {base_path}: {exc}") from exc
    base = lenient_entries(text, str(base_path))
    touched: set[str] = set()
    for entry in entries:
        previous = base.get(entry.entry_id)
        if previous is None:
            touched.add(entry.entry_id)
            continue
        with_anchors = "registered_on" in previous
        current = _entry_as_raw(entry)
        if _fingerprint(previous, with_anchors=with_anchors) != _fingerprint(
            current, with_anchors=with_anchors
        ):
            touched.add(entry.entry_id)
    return touched
