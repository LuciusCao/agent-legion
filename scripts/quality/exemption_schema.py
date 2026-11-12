"""Architecture exemption schema, YAML loader, and calendar expiry validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# #641: strict ISO calendar date — fromisoformat alone also accepts compact forms.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ArchitectureExemption:
    check: str
    path: str
    reason: str
    owner: str
    remove_when: str
    ceiling: int | None = None
    # #641 time-boxed re-file channel: an exemption raised past its monotonic
    # floor + growth allowance must carry a deadline, and the gate hard-fails
    # once the date passes (renew with fresh justification or shrink the file).
    expires: str | None = None


def _parse_exemption(raw: dict[str, Any]) -> ArchitectureExemption:
    """Parse a single exemption entry from the registry YAML.

    An unquoted ``expires: 2026-12-31`` is resolved by PyYAML's timestamp
    resolver to a ``datetime.date`` — the documented plain form must keep
    working, so it is normalized to its ISO string here (codex P1 on #642:
    the validator's ``.strip()`` must never see a raw date object). Any
    other non-string type falls through untouched for the validator to
    reject with a proper error instead of a traceback.
    """
    expires: Any = raw.get("expires")
    if isinstance(expires, date):
        expires = expires.isoformat()
    return ArchitectureExemption(
        check=raw.get("check", ""),
        path=raw.get("path", ""),
        reason=raw.get("reason", ""),
        owner=raw.get("owner", ""),
        remove_when=raw.get("remove_when", ""),
        ceiling=raw.get("ceiling"),
        expires=expires,
    )


def load_exemptions(path: str | Path) -> tuple[ArchitectureExemption, ...]:
    """Load architecture exemptions from a YAML registry file."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_exemptions = data.get("exemptions", [])
    return tuple(_parse_exemption(raw) for raw in raw_exemptions)


def _validate_expires(ex: ArchitectureExemption, today: date) -> str | None:
    """Validate the optional #641 expires deadline for an exemption."""
    if ex.expires is None:
        return None

    # Guard: entries built without the loader (tests, hand-constructed) or
    # carrying an unexpected YAML type must fail validation, not traceback.
    # The Any widen keeps mypy from marking the branch unreachable under
    # the declared ``str | None`` while runtime YAML types can differ.
    expires_value: Any = ex.expires
    if not isinstance(expires_value, str):
        return f"expires {ex.expires!r} must be an ISO date string (YYYY-MM-DD)"

    expires = expires_value.strip()
    if not _ISO_DATE.match(expires):
        return f"expires '{ex.expires}' must be an ISO date (YYYY-MM-DD)"

    deadline = date.fromisoformat(expires)
    if today > deadline:
        return (
            f"exemption expired on {expires} — renew it with a fresh "
            "justification and a later expires date, or shrink the file back "
            "under its ceiling"
        )
    return None
