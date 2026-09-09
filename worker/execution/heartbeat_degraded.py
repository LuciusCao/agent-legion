"""Degraded per-execution beats for the batch coordinator (pre-v5 Host).

Split out of ``heartbeat_batch.py`` (file budget; the #352 exemption text
already nominated this split): when the Host predates the batch endpoint,
the coordinator falls back to one single-beat request per lease — the
identical traffic and semantics of the pre-v5 Worker.

Never serial: each lease's beat rides its own short-lived thread (the
pre-v5 Worker's per-execution thread shape), so one slow Host response
delays only its own lease's renewal, not every later entry's (the serial
sum, N × timeout, is what could cross the lease TTL and get the tail of a
healthy list reclaimed). The coordinator does NOT join the workers: a beat
already in flight when the process dies is a daemon thread and dies with
it, and the requests themselves are idempotent renewals whose only
deadline is the Host-side lease TTL. The per-request timeout cap
(heartbeat_ops.SINGLE_BEAT_TIMEOUT_SECONDS) stays: it is what bounds a
parked worker thread when the Host is merely slow.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from worker.host.heartbeat_ops import SINGLE_BEAT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from worker.execution.heartbeat_batch import _LeaseEntry


def beat_single(client: Any, entries: list[_LeaseEntry]) -> None:
    """One degraded tick: one short-lived daemon thread per entry."""
    for entry in entries:
        thread = threading.Thread(target=_beat_single_one, args=(client, entry), daemon=True)
        thread.start()


def _beat_single_one(client: Any, entry: _LeaseEntry) -> None:
    """One lease's single beat (one degraded tick, one short-lived thread)."""
    try:
        status, cancelled = client.heartbeat(
            entry.execution_id, entry.lease_id, timeout=SINGLE_BEAT_TIMEOUT_SECONDS
        )
    except Exception as exc:
        # #204 broad-except audit: 同 batch 路径的逐拍存活语义，只是粒度
        # 回到单条——一次逃逸只丢这一拍的这一个租约，其余条目与本循环不
        # 受影响。吞是对的：这个线程只有这一次调用，不捕获就是 daemon
        # 线程顶着一个未处理异常退出，除了 traceback 噪音没有任何收益。
        # 日志保全：print 逐条记录。
        print(f"heartbeat error for {entry.execution_id}: {exc}", flush=True)
        return
    if cancelled and entry.on_cancelled is not None:
        entry.on_cancelled(cancelled)
    if status in (401, 409):
        print(f"heartbeat lost ownership for {entry.execution_id}: HTTP {status}", flush=True)
        entry.ownership_lost.set()
    elif status not in (200, 204):
        print(f"heartbeat unexpected status for {entry.execution_id}: HTTP {status}", flush=True)
