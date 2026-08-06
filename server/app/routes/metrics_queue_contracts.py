"""Queue-health card models for the ops-metrics overview response (issue #13).

Split out of ``metrics_summary_contracts.py`` to respect that module's size
budget. The alert distinguishes a blocked queue (claims attempted, every
candidate skipped — histogram carried in ``reasons``) from a stalled one
(no claim activity at all, e.g. the only compatible Worker stopped pulling).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class QueueSummary(BaseModel):
    queued: int
    oldest_queued_at: str | None
    recent_hour_unclaimable_failed: int


class QueueAlert(BaseModel):
    kind: Literal["blocked", "stalled"]
    at: str | None
    reasons: dict[str, int]
