"""Architecture exemption age checks.

Derives the age of an exemption from its tracked remove_when reference and
reports exemptions whose removal condition has been pending for too long.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path, PurePosixPath

from scripts.quality.exemptions import ArchitectureExemption

# Leading ISO date in a referenced file name, e.g. 2026-07-18-plan.md.
_DATE_PREFIX_RE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}(?=-|\.)")

# Date fields in issue frontmatter used when the file name carries no date.
_FRONTMATTER_DATE_RE: re.Pattern[str] = re.compile(
    r"^(?:source_review|closed_at|created_at|date):\s*(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _reference_date(remove_when: str, root: Path) -> date | None:
    """Best-effort date of the tracked removal reference, or None when undatable."""
    file_part = remove_when.split("#", 1)[0]
    name_match = _DATE_PREFIX_RE.match(PurePosixPath(file_part).name)
    if name_match:
        return _parse_iso_date(name_match.group(0))

    referenced = root / file_part
    if referenced.is_file():
        frontmatter = referenced.read_text(encoding="utf-8")[:2000]
        date_match = _FRONTMATTER_DATE_RE.search(frontmatter)
        if date_match:
            return _parse_iso_date(date_match.group(1))

    return None


def exemption_age_warnings(
    exemptions: tuple[ArchitectureExemption, ...],
    base_path: str | Path | None = None,
    *,
    today: date | None = None,
    max_age_days: int = 30,
) -> list[str]:
    """Return non-blocking warnings for exemptions whose removal reference is stale.

    The age of an exemption is derived from the date embedded in its remove_when
    reference (plan file name prefix, or issue frontmatter). References without a
    derivable date are skipped.
    """
    root = Path(base_path) if base_path else Path.cwd()
    reference_today = today or date.today()
    warnings: list[str] = []

    for idx, ex in enumerate(exemptions, start=1):
        reference_date = _reference_date(ex.remove_when, root)
        if reference_date is None:
            continue
        age_days = (reference_today - reference_date).days
        if age_days > max_age_days:
            warnings.append(
                f"exemption {idx} ({ex.check} on {ex.path}): remove_when "
                f"'{ex.remove_when}' is {age_days} days old (limit {max_age_days}); "
                "fulfill the removal condition or re-justify the exemption"
            )

    return warnings
