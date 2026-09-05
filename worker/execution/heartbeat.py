"""Lease-heartbeat facade for one agent execution.

Split out of ``executor.py`` so the executor stays within its size budget.
Since #352 the per-execution heartbeat threads are gone: every claim
registers its lease with the per-Worker batch coordinator
(``heartbeat_batch.py``); ``ExecutionHeartbeat`` keeps the executor/upload
call shape (adopt / stop / proc_ref) and forwards to the registry. A process
that dies unadopted is pruned by the registry's snapshot so the Host's
orphan sweeper can reclaim the lease instead of the worker hiding a zombie.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from typing import Any

from worker.execution.heartbeat_batch import BatchHeartbeatRegistry


class ExecutionHeartbeat:
    """Per-execution heartbeat state, now backed by the batch coordinator.

    Without a registry (single-beat tests) every method degrades to inert
    local event handling."""

    def __init__(
        self,
        stop: threading.Event,
        adopted: threading.Event,
        proc_ref: dict[str, subprocess.Popen[bytes] | None],
        registry: BatchHeartbeatRegistry | None,
        execution_id: str,
    ) -> None:
        self.stop, self.adopted, self.proc_ref = stop, adopted, proc_ref
        self.registry, self.execution_id = registry, execution_id

    def _forward(self, method: str) -> None:
        if self.registry is not None:
            getattr(self.registry, method)(self.execution_id)

    def adopt(self) -> None:
        """Mark the lease as adopted by an upload task (beats outlive proc)."""
        self.adopted.set()
        self._forward("set_adopted")

    def shutdown(self) -> None:
        """Prune the lease from the batch registry (no beat for it anymore)."""
        self.stop.set()
        self._forward("prune")

    def quiesce(self) -> None:
        """Pause this lease's beats (final report in flight)."""
        self._forward("quiesce")

    def resume(self) -> None:
        """Resume this lease's beats (transient report failure backing off)."""
        self._forward("resume")


def start_lease_heartbeat(
    client: Any,
    execution_id: str,
    lease_id: str,
    interval: float,
    ownership_lost: threading.Event,
    on_cancelled: Callable[[list[str]], Any] | None = None,
    registry: BatchHeartbeatRegistry | None = None,
) -> ExecutionHeartbeat:
    """Register one execution's lease with the batch heartbeat coordinator.

    ``interval``/``client`` are accepted for call-shape compatibility; the
    beat period is the coordinator's (per-Worker), not per-execution. Legacy
    callers without a registry get inert defaults — single-beat semantics
    live on in ``lifecycle.py`` and the degraded pre-v5-Host path."""
    stop, adopted = threading.Event(), threading.Event()
    proc_ref: dict[str, subprocess.Popen[bytes] | None] = {"proc": None}
    if registry is not None:
        registry.register(execution_id, lease_id, ownership_lost, on_cancelled)
    return ExecutionHeartbeat(stop, adopted, proc_ref, registry, execution_id)
