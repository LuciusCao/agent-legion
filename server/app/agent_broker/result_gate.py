"""Peak-shaving gate for the Agent Worker result commit (issue #521).

A completion wave (a DAG's same-phase nodes reporting together) hits the
/result endpoint with GIL-bound commit work; unbounded, the commits occupy
the shared threadpool and starve claim/heartbeat on the single-process
control plane. This module owns the gate object and the offload wrapper so
the route file stays at its budget:

- ``build_result_commit_gate`` constructs the semaphore at router wiring
  (reads the knob once, before any request); ``None`` = the disabled
  kill-switch (``max_concurrent_result_commits=0``);
- ``run_gated_result_commit`` parks waiting reporters as coroutines (no
  threadpool tokens) around the blocking offload. Spooling stays outside
  the gate — a slow upload must not occupy a slot. A lease expiring while
  queued just 409s into the sweeper's existing requeue/cleanup path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from starlette import concurrency


def build_result_commit_gate(max_concurrent: int) -> asyncio.Semaphore | None:
    """The /result commit gate; None when disabled (kill-switch)."""
    if max_concurrent <= 0:
        return None
    return asyncio.Semaphore(max_concurrent)


async def run_gated_result_commit(
    gate: asyncio.Semaphore | None,
    commit: Callable[..., None],
    *args: Any,
) -> None:
    """Offload one commit to the threadpool, bounded by the gate when present."""
    if gate is None:
        await concurrency.run_in_threadpool(commit, *args)
        return
    async with gate:
        await concurrency.run_in_threadpool(commit, *args)
