"""Parallel shard fan-out for the relay's batched beat (issue #591).

Split from ``relay_beats.py`` (file-budget discipline, same seam as the
#566 relay split): the sharded batch beat grew its own concurrency body —
per-shard threads with merged verdicts — which does not belong beside the
client-cache lifecycle the beater itself owns.

Shard semantics (#591 follow-up, codex review): one beat request stands
for ``RELAY_BEAT_SHARD`` leases, so a stalled Host must only ever cost its
own shard's tick — later shards still fly (no head-of-line starvation of
the snapshot tail) and shards run concurrently (a serial sweep of N shards
× seconds each could outlast the lease TTL without a single request ever
hitting the per-request timeout). A shard that raises carries NO verdict
(its leases are unknown, not lost — the next tick retries from the same
snapshot; the real deadline stays the lease TTL), so the round reports
whatever the surviving shards learned.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from worker.relay_thread_limiter import ShardThreadLimiter

# Relay beat sharding, deliberately tighter than the executor-side
# MAX_BATCH_HEARTBEATS (256): one beat HTTP request that the Host cannot
# answer inside BATCH_BEAT_TIMEOUT_SECONDS loses only this shard's tick,
# and the remaining shards still try — a completion-wave stall that parks
# the Host's HTTP plane (2026-09-11 incident, issue #591) must not amplify
# into a whole-snapshot lost beat. 64 ≈ one machine's typical in-flight
# slice; shards run in parallel, so the tick wall time is one shard, not
# the sum.
RELAY_BEAT_SHARD = 64

# Timeout for one batch beat request, tightened from the 30s client default
# (2026-09-11 incident, issue #591): 30s sits too close to the 90s lease
# TTL — two consecutive full-length waits already eat the whole renewal
# budget. 10s lets a stalled beat fail fast into the next tick's retry
# while a healthy Host answers a batch renew in well under a second.
BATCH_BEAT_TIMEOUT_SECONDS = 10.0

# Runaway guard for the fan-out join, not a protocol deadline: a daemon
# thread that overstays is abandoned (its result never lands in the merged
# verdicts), so one wedged call can neither pin the relay thread nor block
# the next tick.
_JOIN_MARGIN_SECONDS = 5.0


@dataclass(frozen=True)
class ShardedBeat:
    """One sharded round's outcome; exactly one field set.

    ``verdicts``: whatever the surviving shards learned, snapshotted at
    return (copies — a shard daemon that overstays its join can still land
    appends in the merge lists, never in what the caller holds). ``None``
    verdicts with no signal = the transient round (nothing learned).
    ``degraded`` = any shard saw the 404/405 pre-v5 answer (a protocol
    property one shard settles for the round — the caller flips to single
    beats). ``unauthorized`` = a 401 (token rotated under a re-register —
    the caller drops its cached client so the next snapshot rebuilds).
    """

    verdicts: tuple[list, list] | None = None
    degraded: bool = False
    unauthorized: bool = False


def beat_sharded(
    client: Any,
    leases: list[tuple[str, str]],
    log: Callable[[str], None],
    limiter: ShardThreadLimiter,
) -> ShardedBeat:
    """Beat every shard in parallel; verdicts merge, failures never sink
    neighbours (see ShardedBeat for the outcome shape)."""
    shards = [
        leases[start : start + RELAY_BEAT_SHARD]
        for start in range(0, len(leases), RELAY_BEAT_SHARD)
    ]
    lost: list[tuple[str, str]] = []
    cancelled: list[str] = []
    lock = threading.Lock()
    failures = 0
    endpoint_missing = False
    unauthorized = False

    def beat_one_shard(chunk: list[tuple[str, str]]) -> None:
        nonlocal failures, endpoint_missing, unauthorized
        try:
            outcome = client.heartbeat_batch(chunk, timeout=BATCH_BEAT_TIMEOUT_SECONDS)
        except Exception as exc:
            # #204 broad-except audit: relay 的逐拍存活语义（与 executor
            # 内 batch loop 同族）：传输错误/非 200 的 RuntimeError/畸形
            # 应答都只丢本分片的这一拍，其余分片照常、下一拍全量重来，
            # Host 侧逐项谓词幂等；真正的死线是租约 TTL 与快照停滞停拍。
            # 日志保全：每次失败都 log。
            with lock:
                failures += 1
                if "HTTP 401" in str(exc):
                    unauthorized = True
            log(f"心跳 relay 批量拍失败（{len(chunk)} 租约）：{exc}")
            return
        if outcome is None:
            # 404/405: the Host predates the batch endpoint.
            with lock:
                failures += 1
                endpoint_missing = True
            return
        _status, body = outcome
        lost_ids = set(body.get("lost", []))
        with lock:
            lost.extend(pair for pair in chunk if pair[0] in lost_ids)
            cancelled.extend(body.get("cancelled_execution_ids", []))

    threads: list[threading.Thread] = []
    for shard in shards:
        thread = limiter.start(partial(beat_one_shard, shard))
        if thread is None:
            # Every occupied slot belongs to an earlier request that has not
            # really returned. This shard is unknown for this tick, exactly
            # like a transport failure; retrying by spawning another socket
            # would recreate the resource leak this limiter prevents.
            with lock:
                failures += 1
            log(f"心跳 relay 批量拍跳过（{len(shard)} 租约）：未完成分片已达上限")
            continue
        threads.append(thread)
    # One SHARED deadline for the whole fan-out join (PR #617 review P1-2):
    # a per-thread timeout would let wedged shards stack — N shards × (beat
    # timeout + margin) ≈ 240s at the 1024-lease cap — because `requests`'
    # timeout is per socket-read-op, so a slow-drip Host keeps every shard
    # "alive" past its own join. That serialises the tick into exactly the
    # expiry stall this hotfix exists to prevent. With the budget spent
    # once, every later join returns at (or immediately after) the deadline:
    # the wall time stays one shard's budget, which is all the parallel
    # fan-out ever promised.
    deadline = time.monotonic() + BATCH_BEAT_TIMEOUT_SECONDS + _JOIN_MARGIN_SECONDS
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    if endpoint_missing:
        # Deliberate (PR #617 review note): in a mixed 404/200 round the
        # surviving shards' verdicts are DISCARDED — the 404 is a protocol
        # property one shard settles for the whole round, and the caller's
        # single-beat fallback immediately re-beats every lease, so partial
        # verdicts here would only be re-learned (and re-answered) a beat
        # later; they are not an oversight.
        return ShardedBeat(degraded=True)
    if unauthorized:
        return ShardedBeat(unauthorized=True)
    if failures == len(shards):
        return ShardedBeat()
    # Copies (PR #617 review P1-1): a shard daemon that overstays the join
    # deadline can still append to `lost`/`cancelled` under the lock — hand
    # the caller snapshots so a straggler's late append cannot mutate the
    # lists it is already iterating (the relay's write_beat_result raced
    # exactly that: "list changed size during iteration").
    return ShardedBeat(verdicts=(list(lost), list(cancelled)))
