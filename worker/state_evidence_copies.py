"""Exact-name lifecycle for prep evidence staging and processed copies."""

from __future__ import annotations

from pathlib import Path

EVENTS_COPY_FILENAME = "events.jsonl"
RAW_EVENTS_FILENAME = "events.jsonl.raw"


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
