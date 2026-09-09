"""Shared campaign-feeder value types (#532 PR-C split).

The feeder core (campaign_feeder.py) and its submit-mode branch
(campaign_feeder_submit.py) both speak in these terms; they live here so
the two modules stay acyclic (core → submit → types) at the budget
ceiling that forced the split.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from server.app.services.job_errors import InvalidOperationError


class BatchOutcome(NamedTuple):
    """What one fed batch did (the CAS advance's input)."""

    ids: list[str]
    succeeded: int
    skipped: int
    failed: int
    exhausted: bool  # this slice ended the campaign's target
    next_cursor: str | None  # filter form: the keyset cursor after this page


def copy_progress(campaign: dict[str, Any]) -> dict[str, Any]:
    """Shallow copy of the campaign's stored progress document."""
    progress = campaign.get("progress")
    return dict(progress) if isinstance(progress, dict) else {}


def target_spec(campaign: dict[str, Any]) -> dict[str, Any]:
    """The stored target spec; a non-dict shape is a deterministic failure."""
    spec = campaign.get("target_spec")
    if not isinstance(spec, dict):
        raise InvalidOperationError(
            f"Campaign target spec is corrupt (expected an object, got {type(spec).__name__})"
        )
    return spec
