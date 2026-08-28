"""Lease-heartbeat startup for one agent execution.

Split out of ``executor.py`` so the executor stays within its size budget.
The heartbeat outlives the agent process only once an upload task adopts it;
when the process dies unadopted the loop stops beating so the Host's orphan
sweeper can reclaim the lease instead of the worker hiding a zombie.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from worker.execution.lifecycle import HeartbeatConfig, heartbeat_loop


@dataclass
class ExecutionHeartbeat:
    """State shared between the executor and its lease-heartbeat thread."""

    thread: threading.Thread
    stop: threading.Event
    adopted: threading.Event
    proc_ref: dict[str, subprocess.Popen[bytes] | None]

    def adopt(self) -> None:
        """Mark the lease as adopted by an upload task."""
        self.adopted.set()


def start_lease_heartbeat(
    client: Any,
    execution_id: str,
    lease_id: str,
    interval: float,
    ownership_lost: threading.Event,
    on_cancelled: Callable[[list[str]], Any] | None = None,
) -> ExecutionHeartbeat:
    """Start the daemon heartbeat thread for one execution's lease."""
    stop = threading.Event()
    adopted = threading.Event()
    proc_ref: dict[str, subprocess.Popen[bytes] | None] = {"proc": None}
    config = HeartbeatConfig(
        client=client,
        execution_id=execution_id,
        lease_id=lease_id,
        stop=stop,
        interval=interval,
        ownership_lost=ownership_lost,
        proc_ref=proc_ref,
        adopted=adopted,
        on_cancelled=on_cancelled,
    )
    thread = threading.Thread(target=heartbeat_loop, args=(config,), daemon=True)
    thread.start()
    return ExecutionHeartbeat(thread=thread, stop=stop, adopted=adopted, proc_ref=proc_ref)
