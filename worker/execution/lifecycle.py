"""Lease-heartbeat lifecycle for Agent Worker executions."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HeartbeatConfig:
    """One lease-heartbeat loop's inputs.

    ``ownership_lost`` / ``proc_ref`` / ``adopted`` track the agent process;
    the upload side leaves them as inert defaults. ``on_cancelled`` receives
    the protocol-v2 heartbeat body's cancelled execution ids (batch 2)."""

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
    on_cancelled: Callable[[list[str]], Any] | None = None


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
            status, cancelled = config.client.heartbeat(execution_id, config.lease_id)
        except Exception as exc:  # transient network error: keep beating
            # #204 broad-except audit: 心跳线程的存活语义。单次心跳的逃逸
            # 族（requests 传输错误、畸形应答等）逐拍独立——失败只意味着这
            # 一拍没送达，interval 后的下一拍重新证明存活，真正的死线是
            # Host 侧租约 TTL。吞掉并 continue 是对的：让一次失败杀死这个
            # daemon 线程反而造成租约静默过期、执行被 Host 重调度。结果空间
            # 是这一拍的丢拍，无状态残留。日志保全：每次失败都 print
            # （flush=True），持续性故障按 interval 反复可见，不会静默。
            print(f"heartbeat error for {execution_id}: {exc}", flush=True)
            continue
        if cancelled and config.on_cancelled is not None:
            config.on_cancelled(cancelled)
        if status in (401, 409):
            print(f"heartbeat lost ownership for {execution_id}: HTTP {status}", flush=True)
            config.ownership_lost.set()
            return
        # 200 = protocol v2 (cancel body, possibly empty); 204 = legacy v1.
        if status not in (200, 204):
            print(f"heartbeat unexpected status for {execution_id}: HTTP {status}", flush=True)
