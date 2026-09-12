"""Beat transport for the supervisor-side heartbeat relay (split from
``heartbeat_relay.py`` for the file budget): batch-first beats with the
pre-v5 degraded fallback, the client cache keyed on (host_url, token), and
the stale-stall control-plane ping.

停拍 ≠ 失联（PR #572 codex P1）: when the lease snapshot goes stale the
relay stops RENEWING leases (the Host's 2×TTL hard bound reclaims them),
but keeps proving the worker is alive with a lightweight authenticated
read (``get_self`` → authorize → ``record_seen`` → ``last_seen_at``) —
that read carries no lease-renewal effect. Without it the phase-1 deferral
(#570) cannot tell "execution plane starved" from "worker truly offline"
once ``last_seen_at`` ages past the online window.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from worker.host.client import Client
from worker.host.heartbeat_ops import SINGLE_BEAT_TIMEOUT_SECONDS
from worker.relay_shards import BATCH_BEAT_TIMEOUT_SECONDS, RELAY_BEAT_SHARD, beat_sharded
from worker.relay_thread_limiter import ShardThreadLimiter

__all__ = [
    "BATCH_BEAT_TIMEOUT_SECONDS",
    "RELAY_BEAT_SHARD",
    "RelayBeater",
]


class RelayBeater:
    """Owns the relay's Host client and every beat/ping call shape."""

    def __init__(
        self,
        *,
        log: Callable[[str], None],
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._log = log
        self._make_client = client_factory or (lambda host, token: Client(host, token=token))
        self._client: Any = None
        self._client_key: tuple[str, str] | None = None
        # Shared across ticks: a timed-out request keeps its slot until its
        # socket call really exits, so a slow Host cannot grow daemon threads
        # without bound on every relay interval.
        self._shard_threads = ShardThreadLimiter()
        self.degraded = False
        self._ping_error_logged = False

    def ensure_client(self, host_url: str, token: str) -> Any:
        """The cached client, rebuilt when the (host, token) pair changes."""
        if self._client is None or self._client_key != (host_url, token):
            self._client = self._make_client(host_url, token)
            self._client_key = (host_url, token)
        return self._client

    def control_plane_ping(self, host_url: str, token: str) -> None:
        """One authenticated read with no lease effect (stale-stall liveness).

        401 semantics match the beat path: drop the cached client so a
        rotated token rebuilds from the next snapshot."""
        try:
            self.ensure_client(host_url, token).get_self()
        except Exception as exc:
            # #204 broad-except audit: 控制面 ping 是停拍期的旁路自证——传输
            # 错误/鉴权拒绝/畸形应答都只丢这一拍，下一拍（一个 relay 间隔后）
            # 重试；让异常逃逸会杀死 relay 线程。日志保全：每停滞 episode
            # 记一次（成功后重置），401 额外丢缓存 client 等 token 轮换。
            if "401" in str(exc):
                self._client = None
            if not self._ping_error_logged:
                self._ping_error_logged = True
                self._log(f"控制面 ping 失败：{exc}（租约停拍中，控制面信号中断）")
        else:
            self._ping_error_logged = False

    def beat(self, leases: list[tuple[str, str]]) -> tuple[Any, Any]:
        """Beat the whole snapshot; (None, None) = transient, retry next tick."""
        if self.degraded:
            return self._beat_singles(leases)
        outcome = self._beat_batch(leases)
        return (None, None) if outcome is None else outcome

    def _beat_batch(self, leases: list[tuple[str, str]]) -> tuple[list, list] | None:
        """Sharded parallel batch beat; the concurrency body lives in
        ``relay_shards`` (file-budget split, same seam as the #566 relay
        modules). ``(None, None)`` at the ``beat`` layer = transient."""
        outcome = beat_sharded(self._client, leases, self._log, self._shard_threads)
        if outcome.degraded:
            self.degraded = True
            self._log("Host 无批量心跳端点，relay 降级为逐租约心跳")
            return self._beat_singles(leases)
        if outcome.unauthorized:
            # 401 (token rotated under a re-register): drop the cached client,
            # the next snapshot rebuilds with the fresh token.
            self._client = None
            return None
        return outcome.verdicts

    def _beat_singles(self, leases: list[tuple[str, str]]) -> tuple[list, list]:
        """Degraded mode: thread-per-lease beats (a slow Host parks only its own lease)."""
        lost: list[tuple[str, str]] = []
        cancelled: list[str] = []
        lock = threading.Lock()

        def beat_one(execution_id: str, lease_id: str) -> None:
            try:
                status, cancelled_ids = self._client.heartbeat(
                    execution_id, lease_id, timeout=SINGLE_BEAT_TIMEOUT_SECONDS
                )
            except Exception as exc:
                # #204 broad-except audit: 单租约丢拍语义——本线程只有这一次
                # 调用，异常不逃逸成 daemon 线程 traceback；下一拍重试。
                self._log(f"心跳 relay 单拍失败 {execution_id}：{exc}")
                return
            with lock:
                if status in (401, 409):
                    lost.append((execution_id, lease_id))
                cancelled.extend(cancelled_ids)

        threads = [threading.Thread(target=beat_one, args=pair, daemon=True) for pair in leases]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=SINGLE_BEAT_TIMEOUT_SECONDS + 1)
        return lost, cancelled
