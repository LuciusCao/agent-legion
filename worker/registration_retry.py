"""Keep the Worker alive while its Host is temporarily unavailable."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from worker.host_client import Client, WorkerAuthError


def register_from_config(
    client: Client, config: dict[str, Any], stop: threading.Event
) -> tuple[float, bool | None]:
    management_token = Path(str(config["register_token_file"])).read_text(encoding="utf-8").strip()
    poll_interval = float(config.get("poll_interval_seconds", 2))
    return (
        poll_interval,
        register_with_retry(client, config, management_token, stop, poll_interval),
    )


def register_with_retry(
    client: Client,
    config: dict[str, Any],
    management_token: str,
    stop: threading.Event,
    initial_backoff: float,
) -> bool | None:
    backoff = max(0.2, initial_backoff)
    while not stop.is_set():
        try:
            client.register(config, management_token)
            return True
        except WorkerAuthError as exc:
            print(f"Agent Worker registration rejected: {exc}", flush=True)
            return False
        except Exception as exc:
            print(
                f"Agent Worker registration unavailable: {exc}; retrying in {backoff:.1f}s",
                flush=True,
            )
            stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)
    return None
