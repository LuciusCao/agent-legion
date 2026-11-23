"""Exact-name lifecycle for prep evidence staging and processed copies."""

from __future__ import annotations

import re
from pathlib import Path

EVENTS_COPY_FILENAME = "events.jsonl"
RAW_EVENTS_FILENAME = "events.jsonl.raw"


def _safe_segment(value: str, fallback: str) -> str:
    """Neutralize one incident-name segment from the wire."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("._")
    return (cleaned or fallback)[:64]


def discard_event_copies(target: Path, *, include_processed: bool) -> list[str]:
    """Best-effort unlink of only this incident's processed/raw event copies.

    Returns bounded filename/error diagnostics for the incident record. Other
    evidence, including emergency and recovered streams, is never addressed.
    An unlink failure leaves the explicitly named file for startup reclamation;
    it is never converted into a successful copy state.
    """
    names = (
        (EVENTS_COPY_FILENAME, RAW_EVENTS_FILENAME) if include_processed else (RAW_EVENTS_FILENAME,)
    )
    errors = []
    for name in names:
        try:
            (target / name).unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    return errors


def with_copy_cleanup_errors(state: str, errors: list[str]) -> str:
    """Never report plain absent/unreadable/discarded while stale copies remain."""
    if not errors:
        return state
    return f"{state}; evidence copy cleanup failed: {'; '.join(errors)}"


def validate_scanned_events(events: Path, original: int, completed: bool) -> None:
    """Fail closed on raw survivors unless a scanned events tree vanished."""
    try:
        raw_survived = original == 0 and events.is_file() and events.stat().st_size > 0
    except OSError as exc:
        from worker.state_evidence import tree_missing_inside

        if not completed or not tree_missing_inside(exc, events.parent):
            raise
        raw_survived = False
    if raw_survived:
        raise RuntimeError(f"pi events scan failed; the raw events file survived: {events}")
