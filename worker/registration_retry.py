"""Keep the Worker alive while its Host is temporarily unavailable."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import requests

from worker._retry import run_with_retry
from worker.host_client import Client, TransientHostError, WorkerAuthError
from worker.registration_token import registration_tokens

_last_registration_workspaces: list[dict[str, Any]] = []


def register_from_config(
    client: Client, config: dict[str, Any], stop: threading.Event, state_dir: Path
) -> tuple[float, bool | None]:
    """Register with every configured scoped token (issue #35).

    All tokens are presented in one registration; the Host resolves the union
    scope and rejects the whole call when any token is unknown or revoked —
    a partial registration can never silently narrow the worker's scope."""
    tokens = [row["token"] for row in registration_tokens(config, state_dir)]
    poll_interval = float(config.get("poll_interval_seconds", 2))
    return (
        poll_interval,
        register_with_retry(client, config, tokens, stop, poll_interval),
    )


def register_with_retry(
    client: Client,
    config: dict[str, Any],
    management_tokens: list[str],
    stop: threading.Event,
    initial_backoff: float,
) -> bool | None:
    def attempt() -> bool:
        document = client.register(config, management_tokens)
        # 注册成功即记住 Host 汇报的 workspace 明细（id+name）；host_status_sync
        # 把它随每次 remote 状态发布到状态文件，控制台据此给每张 token 卡片
        # 标注对应的 workspace 名称。
        global _last_registration_workspaces
        _last_registration_workspaces = list(document.get("workspaces", []))
        return True

    try:
        return run_with_retry(
            attempt,
            # Only transport-level failures are "Host temporarily unavailable":
            # requests raises RequestException subclasses, and the client
            # answers 5xx/429 with TransientHostError (a RequestException
            # subclass itself). A blanket (Exception,) here would also retry
            # TypeError/KeyError bugs inside the attempt forever, disguising
            # them as network outages; those must crash loudly instead.
            retriable=(requests.RequestException, TransientHostError),
            terminal=(WorkerAuthError,),
            base_seconds=max(0.2, initial_backoff),
            cap_seconds=60.0,
            stop=stop,
            on_retry=lambda exc, backoff: print(
                f"Agent Worker registration unavailable: {exc}; retrying in {backoff:.1f}s",
                flush=True,
            ),
        )
    except WorkerAuthError as exc:
        print(f"Agent Worker registration rejected: {exc}", flush=True)
        return False


def last_registration_workspaces() -> list[dict[str, Any]]:
    """Workspaces reported by the most recent successful registration."""
    return list(_last_registration_workspaces)
