"""Architecture exemption registry loader and validator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# Case-insensitive vague reasons that are not specific enough to govern an exemption.
_VAGUE_REASONS: tuple[str, ...] = ("legacy", "temporary", "follow up")

# Allowed prefixes for removal references.
_PLAN_PREFIXES: tuple[str, ...] = ("docs/superpowers/plans/", "docs/architecture/")
_ISSUE_PREFIXES: tuple[str, ...] = ("issues/open/", "issues/closed/")

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
    """Parse a single exemption entry from the registry YAML."""
    return ArchitectureExemption(
        check=raw.get("check", ""),
        path=raw.get("path", ""),
        reason=raw.get("reason", ""),
        owner=raw.get("owner", ""),
        remove_when=raw.get("remove_when", ""),
        ceiling=raw.get("ceiling"),
        expires=raw.get("expires"),
    )


def load_exemptions(path: str | Path) -> tuple[ArchitectureExemption, ...]:
    """Load architecture exemptions from a YAML registry file."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_exemptions = data.get("exemptions", [])
    return tuple(_parse_exemption(raw) for raw in raw_exemptions)


def _reason_is_vague(reason: str) -> bool:
    """Return True when the reason contains a forbidden vague term."""
    lowered = reason.lower()
    return any(vague in lowered for vague in _VAGUE_REASONS)


def _validate_remove_when(remove_when: str, base_path: Path) -> str | None:
    """Return an error message when remove_when does not reference a removal artifact.

    docs/superpowers/ and issues/ are internal documents that are not published
    with the repository. When the corresponding directory tree is absent (for
    example in an open-source checkout), the file-existence check is skipped
    instead of failing; when the directory exists, a missing referenced file is
    still an error. docs/architecture/ is tracked, so a reference there is
    always verifiable from a fresh checkout — prefer it for new exemptions.
    """
    remove_when = remove_when.strip()
    if not remove_when:
        return "remove_when is empty"

    # Strip anchor so the file path can be resolved.
    file_part = remove_when.split("#", 1)[0]

    is_plan = any(remove_when.startswith(prefix) for prefix in _PLAN_PREFIXES)
    is_issue = any(remove_when.startswith(prefix) for prefix in _ISSUE_PREFIXES)

    if not (is_plan or is_issue):
        return (
            f"remove_when '{remove_when}' must reference one of {_PLAN_PREFIXES + _ISSUE_PREFIXES}"
        )

    referenced = base_path / file_part
    if not referenced.exists():
        if remove_when.startswith("docs/superpowers/"):
            anchor = base_path / "docs" / "superpowers"
        elif is_plan:
            anchor = base_path / "docs" / "architecture"
        else:
            anchor = base_path / "issues"
        if anchor.is_dir():
            return f"remove_when references missing file '{file_part}'"

    return None


def _validate_expires(ex: ArchitectureExemption, prefix: str, today: date) -> str | None:
    """Validate the optional #641 expires deadline for an exemption."""
    if ex.expires is None:
        return None

    expires = ex.expires.strip()
    if not _ISO_DATE.match(expires):
        return f"expires '{ex.expires}' must be an ISO date (YYYY-MM-DD)"

    deadline = date.fromisoformat(expires)
    if today > deadline:
        return (
            f"{prefix}: exemption expired on {expires} — renew it with a fresh "
            "justification and a later expires date, or shrink the file back "
            "under its ceiling"
        )
    return None


def _validate_ceiling(
    ex: ArchitectureExemption, root: Path, prefix: str, growth_allowance: int
) -> str | None:
    """Validate the optional ceiling field for an exemption."""
    if ex.ceiling is None:
        if ex.check == "architecture.file_budget":
            return "ceiling is required for architecture.file_budget"
        return None

    if type(ex.ceiling) is not int or isinstance(ex.ceiling, bool):
        return "ceiling must be a positive non-boolean integer"

    if ex.ceiling <= 0:
        return "ceiling must be a positive non-boolean integer"

    if ex.check != "architecture.file_budget":
        return "ceiling is only allowed for architecture.file_budget"

    if ex.check == "architecture.file_budget" and ex.path.strip():
        file_path = root / ex.path
        if file_path.is_file():
            from scripts.architecture.effective_lines import count_effective_lines

            actual = count_effective_lines(file_path)
            # #641: the growth allowance band may legally separate the file's
            # actual size from its registered ceiling, so only a ceiling below
            # actual minus the band is a misregistered exemption.
            if ex.ceiling + growth_allowance < actual:
                return (
                    f"ceiling {ex.ceiling} is below actual effective line count "
                    f"({actual} lines) even with the growth allowance "
                    f"({growth_allowance} lines)"
                )

    return None


def validate_exemptions(
    exemptions: tuple[ArchitectureExemption, ...],
    base_path: str | Path | None = None,
    *,
    today: date | None = None,
    growth_allowance: int = 0,
) -> list[str]:
    """Validate a loaded exemption registry and return a list of concise violations."""
    root = Path(base_path) if base_path else Path.cwd()
    errors: list[str] = []

    for idx, ex in enumerate(exemptions, start=1):
        prefix = f"exemption {idx}"

        if not ex.check.strip():
            errors.append(f"{prefix}: check is empty")

        if not ex.path.strip():
            errors.append(f"{prefix}: path is empty")
        elif ex.path.strip() == "*":
            errors.append(f"{prefix}: path must not be a wildcard-only value")

        if not ex.reason.strip():
            errors.append(f"{prefix}: reason is empty")
        elif _reason_is_vague(ex.reason):
            errors.append(
                f"{prefix}: reason is too vague (avoid 'legacy', 'temporary', 'follow up')"
            )

        if not ex.owner.strip():
            errors.append(f"{prefix}: owner is empty")

        remove_when_error = _validate_remove_when(ex.remove_when, root)
        if remove_when_error:
            errors.append(f"{prefix}: {remove_when_error}")

        ceiling_error = _validate_ceiling(ex, root, prefix, growth_allowance)
        if ceiling_error:
            errors.append(f"{prefix}: {ceiling_error}")

        expires_error = _validate_expires(ex, prefix, today or date.today())
        if expires_error:
            errors.append(f"{prefix}: {expires_error}")

    return errors
