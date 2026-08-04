"""Lease-heartbeat lifecycle for Agent Worker executions."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HeartbeatConfig:
    """One lease-heartbeat loop's inputs.

    ``ownership_lost`` / ``proc_ref`` / ``adopted`` track the agent process;
    the upload side leaves them as inert defaults."""

    client: Any
    execution_id: str
    lease_id: str
    stop: threading.Event
    interval: float
    ownership_lost: threading.Event = field(default_factory=threading.Event)
    proc_ref: dict[str, subprocess.Popen[bytes] | None] = field(
        default_factory=lambda: {"proc": None}
    )
    adopted: threading.Event = field(default_factory=threading.Event)


def heartbeat_loop(config: HeartbeatConfig) -> None:
    """Beat until stopped; 401/409 or a dead, unadopted agent process stops it."""
    execution_id = config.execution_id
    while not config.stop.wait(config.interval):
        proc = config.proc_ref.get("proc")
        if proc is not None and proc.poll() is not None and not config.adopted.is_set():
            print(
                f"heartbeat stopping for {execution_id}: agent process exited unadopted",
                flush=True,
            )
            return
        try:
            status = config.client.heartbeat(execution_id, config.lease_id)
        except Exception as exc:  # transient network error: keep beating
            print(f"heartbeat error for {execution_id}: {exc}", flush=True)
            continue
        if status in (401, 409):
            print(f"heartbeat lost ownership for {execution_id}: HTTP {status}", flush=True)
            config.ownership_lost.set()
            return
        if status != 204:
            print(f"heartbeat unexpected status for {execution_id}: HTTP {status}", flush=True)
