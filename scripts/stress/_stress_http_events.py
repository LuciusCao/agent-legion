"""HTTP-backed event recorder used by the synthetic load generator."""

from __future__ import annotations

import logging
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

    def record_batch(self, events: list[tuple[str, str]]) -> tuple[int, float | None]:
        if not events:
            return 0, None
        payload: dict[str, Any] = {
            "events": [{"job_id": job_id, "kind": kind} for job_id, kind in events]
        }
        url = f"{self.base_url}/api/workspaces/{self.workspace_id}/events/stress"
        try:
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            recorded = data.get("recorded", len(events))
            recorded_at = data.get("recorded_at")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record stress events: %s", exc)
            recorded = 0
            recorded_at = None
        return recorded, recorded_at
