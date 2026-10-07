"""Flaky registry schema and loader (``tests/flaky_registry.yaml``).

Clock-free rules enforced at load time (#941, #917 T-4): every nodeid is
registered at most once (``scripts/check_reruns.py`` keys entries by nodeid,
so a second entry silently shadowed the first), and every non-recurring entry
carries a ``registered_on`` date (plus ``extended_on`` after a reviewed
extension) with its deadline at most ``MAX_DEADLINE_WINDOW_DAYS`` after that
anchor. Nothing here reads today's date: whether a deadline has passed is the
nightly ``check_reruns.py --check-deadlines`` job's call. Registries of
other revisions (PR base, maintained branches) are read leniently by
``flaky_registry_lenient``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

# A flake gets one short fix window per review; anything longer is a parked
# exemption that nobody revisits (#917 T-4).
MAX_DEADLINE_WINDOW_DAYS = 45


class RegistryError(ValueError):
    """Raised when the flaky registry fails schema validation."""


@dataclass(frozen=True)
class RegistryEntry:
    entry_id: str
    owner: str
    reason: str
    observed: str
    nodeid: str | None
    scope: str | None
    deadline: date | None
    recurring: bool
    registered_on: date | None = None
    extended_on: date | None = None


def _parse_entry(raw: object, index: int) -> RegistryEntry:
    where = f"entries[{index}]"
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: entry must be a mapping")

    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise RegistryError(f"{where}: missing or invalid 'id'")
    where = f"entry {entry_id}"

    for field in ("owner", "reason", "observed"):
        if not isinstance(raw.get(field), str) or not str(raw[field]).strip():
            raise RegistryError(f"{where}: missing or invalid '{field}'")

    nodeid = raw.get("nodeid")
    scope = raw.get("scope")
    if (nodeid is None) == (scope is None):
        raise RegistryError(f"{where}: exactly one of 'nodeid' or 'scope' is required")
    for name, value in (("nodeid", nodeid), ("scope", scope)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RegistryError(f"{where}: '{name}' must be a non-empty string")

    recurring = bool(raw.get("recurring", False))
    registered_on = _parse_date(raw, "registered_on", where)
    extended_on = _parse_date(raw, "extended_on", where)
    if recurring:
        if raw.get("deadline") is not None:
            raise RegistryError(f"{where}: recurring entries must not set 'deadline'")
        if extended_on is not None:
            raise RegistryError(f"{where}: recurring entries must not set 'extended_on'")
        deadline = None
    else:
        deadline = _parse_date(raw, "deadline", where)
        if deadline is None:
            raise RegistryError(f"{where}: non-recurring entries require 'deadline'")
        _check_deadline_window(where, deadline, registered_on, extended_on)

    return RegistryEntry(
        entry_id=entry_id.strip(),
        owner=str(raw["owner"]).strip(),
        reason=str(raw["reason"]).strip(),
        observed=str(raw["observed"]).strip(),
        nodeid=nodeid.strip() if isinstance(nodeid, str) else None,
        scope=scope.strip() if isinstance(scope, str) else None,
        deadline=deadline,
        recurring=recurring,
        registered_on=registered_on,
        extended_on=extended_on,
    )


def _parse_date(raw: dict, field: str, where: str) -> date | None:
    value = raw.get(field)
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RegistryError(f"{where}: invalid '{field}' {value!r}") from exc


def _check_deadline_window(
    where: str, deadline: date, registered_on: date | None, extended_on: date | None
) -> None:
    """Clock-free window rule: the deadline is judged against the entry's own
    anchor date (``extended_on`` when present, else ``registered_on``), never
    against today, so the unit tier gives the same verdict on every day."""
    if registered_on is None:
        raise RegistryError(f"{where}: non-recurring entries require 'registered_on'")
    if extended_on is not None and extended_on < registered_on:
        raise RegistryError(f"{where}: 'extended_on' must not precede 'registered_on'")
    anchor = extended_on or registered_on
    if deadline <= anchor:
        raise RegistryError(f"{where}: 'deadline' {deadline} must be after {anchor}")
    if deadline > anchor + timedelta(days=MAX_DEADLINE_WINDOW_DAYS):
        raise RegistryError(
            f"{where}: 'deadline' {deadline} exceeds the "
            f"{MAX_DEADLINE_WINDOW_DAYS}-day window from {anchor}; fix the flake "
            "or extend with a reviewed reason and a new 'extended_on'"
        )


def load_registry(path: Path) -> list[RegistryEntry]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RegistryError(f"{path}: top-level 'entries' list is required")
    entries = [_parse_entry(raw, index) for index, raw in enumerate(data["entries"])]
    seen: set[str] = set()
    nodeid_owner: dict[str, str] = {}
    for entry in entries:
        if entry.entry_id in seen:
            raise RegistryError(f"duplicate entry id {entry.entry_id}")
        seen.add(entry.entry_id)
        if entry.nodeid is not None:
            if entry.nodeid in nodeid_owner:
                raise RegistryError(
                    f"duplicate nodeid {entry.nodeid} in {nodeid_owner[entry.nodeid]} "
                    f"and {entry.entry_id}; merge the observations into one entry"
                )
            nodeid_owner[entry.nodeid] = entry.entry_id
    return entries
