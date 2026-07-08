"""HTTP-backed event recorder used by the synthetic load generator."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class StressHttpEventRecorder:
    def __init__(
        self,
        base_url: str,
        workspace_id: str,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        self.session = session or requests.Session()

    def record_batch(self, events: list[tuple[str, str]]) -> tuple[int, float]:
        if not events:
            return 0, 0.0
        payload: dict[str, Any] = {
            "events": [{"job_id": job_id, "kind": kind} for job_id, kind in events]
        }
        url = f"{self.base_url}/api/workspaces/{self.workspace_id}/events/stress"
        start = time.monotonic()
        try:
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            recorded = response.json().get("recorded", len(events))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record stress events: %s", exc)
            recorded = 0
        elapsed = time.monotonic() - start
        return recorded, elapsed
