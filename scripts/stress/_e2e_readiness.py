"""Readiness helpers for the end-to-end stress runner."""

from __future__ import annotations

import time
from typing import Any


def _request_json(url: str, timeout: float) -> Any:
    import requests

    response = requests.get(url, timeout=timeout)
    if response.status_code == 200:
        return response.json()
    return None


def wait_for_server(base_url: str, timeout: float = 60.0) -> bool:
    """Wait until the backend HTTP server responds to health checks."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = _request_json(f"{base_url}/api/workspaces", timeout=2.0)
            if data is not None:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def wait_for_snapshot_readiness(
    base_url: str,
    workspace_id: str,
    min_jobs: int = 1,
    timeout: float = 120.0,
) -> bool:
    """Wait until the workspace has at least ``min_jobs`` total jobs seeded.

    This acts as a seed-ready barrier so the frontend stress opens the page
    after the backend simulator has reached the target scale. The check uses
    the ``stats`` aggregate from the snapshot endpoint rather than the single
    page of ``jobs``, so it works for 10k/50k-job workspaces.
    """
    deadline = time.monotonic() + timeout
    url = f"{base_url}/api/workspaces/{workspace_id}/jobs/snapshot"
    while time.monotonic() < deadline:
        try:
            data = _request_json(url, timeout=5.0)
            if data is not None:
                stats = data.get("stats", {})
                total_jobs = sum(stats.values())
                if total_jobs >= min_jobs:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False
