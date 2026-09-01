"""Exemption expiry detection against a tracked cache of GitHub issue states.

Issue-anchored exemptions (``remove_when`` of the form
``issues/open/github.com/<owner>/<repo>/issues/<n>``) expire when the anchor
issue closes: the removal condition is then satisfied and the exemption must
be fulfilled (split the file below its baseline ceiling and drop the entry)
or re-anchored to a live open issue. Live GitHub state is network-dependent,
so gates read a tracked cache (``config/architecture/issue-states.json``)
that ``scripts/refresh_issue_states.py`` refreshes via ``gh`` — the check
itself stays offline and deterministic. An issue absent from the cache is
treated as unknown (never expired), and a missing cache file only disables
the cache lookup: a ``remove_when`` that itself declares ``issues/closed/``
still fails, because that declaration is self-contained.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.quality.exemptions import ArchitectureExemption

# Tracked cache consumed by check_invariants; written only by the refresh
# script (make architecture-issue-states / nightly exemption-expiry job).
MANIFEST_RELATIVE_PATH = "config/architecture/issue-states.json"
MANIFEST_VERSION = 1

VALID_STATES: frozenset[str] = frozenset({"open", "closed"})

_ISSUE_REFERENCE_RE: re.Pattern[str] = re.compile(
    r"^issues/(?P<declared>open|closed)/(?P<reference>github\.com/[^/\s]+/[^/\s]+/issues/\d+)$"
)


class IssueStateCacheError(ValueError):
    """Malformed issue-state cache manifest."""


def parse_issue_reference(remove_when: str) -> tuple[str, str] | None:
    """Split an issue-anchored remove_when into (declared_state, reference).

    Returns None for every other remove_when form (tracked plans, local
    ``issues/open/123.md`` documents) — those carry no GitHub issue state.
    """
    match = _ISSUE_REFERENCE_RE.match(remove_when.strip())
    if not match:
        return None
    return match.group("declared"), match.group("reference")


def load_issue_states(path: str | Path) -> dict[str, str]:
    """Load and validate the issue-state cache manifest.

    Raises IssueStateCacheError for malformed files so the gate can fail
    loudly instead of silently skipping expiry detection.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IssueStateCacheError(f"malformed JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise IssueStateCacheError(f"{path}: root must be a mapping")

    unknown = set(raw) - {"version", "updated_at", "issues"}
    missing = {"version", "issues"} - set(raw)
    if missing or unknown:
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        if unknown:
            parts.append(f"unknown fields: {sorted(unknown)}")
        raise IssueStateCacheError(f"{path}: invalid manifest structure; {'; '.join(parts)}")

    if raw.get("version") != MANIFEST_VERSION:
        raise IssueStateCacheError(f"{path}: unsupported manifest version {raw.get('version')!r}")

    issues = raw.get("issues")
    if not isinstance(issues, dict):
        raise IssueStateCacheError(f"{path}: issues must be a mapping")

    states: dict[str, str] = {}
    for reference, state in issues.items():
        if not isinstance(reference, str) or not reference.strip():
            raise IssueStateCacheError(f"{path}: issue reference keys must be non-empty strings")
        if state not in VALID_STATES:
            raise IssueStateCacheError(
                f"{path}: issue '{reference}' has unsupported state {state!r} "
                f"(expected one of {sorted(VALID_STATES)})"
            )
        states[reference] = state
    return states


def expired_issue_errors(
    exemptions: tuple[ArchitectureExemption, ...],
    base_path: str | Path | None = None,
) -> list[str]:
    """Return errors for exemptions whose issue anchor is already closed."""
    root = Path(base_path) if base_path else Path.cwd()

    manifest_path = root / MANIFEST_RELATIVE_PATH
    states: dict[str, str] | None = None
    if manifest_path.is_file():
        try:
            states = load_issue_states(manifest_path)
        except IssueStateCacheError as exc:
            return [str(exc)]

    errors: list[str] = []
    for idx, ex in enumerate(exemptions, start=1):
        parsed = parse_issue_reference(ex.remove_when)
        if parsed is None:
            continue
        declared, reference = parsed
        closed = declared == "closed" or states is not None and states.get(reference) == "closed"
        if not closed:
            continue
        errors.append(
            f"exemption {idx} ({ex.check} on {ex.path}): exemption expired — "
            f"remove_when '{ex.remove_when.strip()}' references closed issue {reference}; "
            "fulfill the removal condition and drop the exemption, "
            "or re-anchor it to an open issue"
        )
    return errors


__all__ = [
    "MANIFEST_RELATIVE_PATH",
    "MANIFEST_VERSION",
    "VALID_STATES",
    "IssueStateCacheError",
    "expired_issue_errors",
    "load_issue_states",
    "parse_issue_reference",
]
