"""Supervisor-side lease heartbeat relay (#566 phase 2).

The batch lease heartbeat used to run as a daemon thread inside the
executor process — exactly the process whose GIL saturates under load, so
the beats starved while the machine still served work (phase 1's Host-side
deferral covers the gap; this relay removes it). The relay runs in the
supervisor process (idle by design): it reads the executor's lease
snapshot (``lease_snapshot.py``), beats those leases with the snapshot's
worker token, and writes the Host's verdicts (lost / cancelled) back to
the beat-result file for the executor to apply.

Safety rails:
- Only beats while the snapshot's pid is a live process AND the snapshot
  is fresh — a brain-dead executor's leases must expire on the normal Host
  TTL path, not be renewed forever from a frozen snapshot.
- Every relayed beat authenticates the worker (the Host's authenticate
  path refreshes ``last_seen_at``), so the control plane stays fresh even
  when the executor's own claim/status loop is starved — this is what lets
  the phase-1 deferral engage in the pure-saturation scenario.
- 401 (token rotated by a re-registration the snapshot predates) drops the
  cached client; the next fresh snapshot carries the new token.

Liveness (PR #572 review): the result file is rewritten EVERY tick that
reaches the beat stage — even with empty verdicts or a transient beat
failure — so the executor's watchdog (``relay_sync.py``) can tell
"relay alive, nothing to report" from "relay dead / supervisor hung"
(leases would otherwise silently expire and double-run). Degraded mode
(pre-v5 Host) resets when the snapshot's pid changes: an executor restart
re-probes the batch endpoint instead of pinning single beats forever.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from worker.execution.heartbeat_batch import MAX_BATCH_HEARTBEATS, clamp_batch_interval
from worker.host.client import Client
from worker.host.heartbeat_ops import SINGLE_BEAT_TIMEOUT_SECONDS
from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_FILENAME,
    SNAPSHOT_STALE_SECONDS,
    read_snapshot,
    snapshot_stale,
    write_beat_result,
)


def _pid_alive(pid: Any) -> bool:
    """Snapshot pids are self-reported; a dead executor must stop the beats."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else — still alive
    except (OSError, TypeError, ValueError):
        return False
    return True


class HeartbeatRelay:
    """One worker's supervisor-side beat loop; ``tick`` is the testable unit."""

    def __init__(
        self,
        *,
        state_dir: Path,
        get_config: Callable[[], dict[str, Any]],
        stop: threading.Event,
        log: Callable[[str], None],
        client_factory: Callable[[str, str], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._state_dir = state_dir
        self._get_config = get_config
        self._stop = stop
        self._log = log
        self._make_client = client_factory or (lambda host, token: Client(host, token=token))
        self._clock = clock
        self._client: Any = None
        self._client_key: tuple[str, str] | None = None
        self._degraded = False
        self._stale_logged = False
        self._result_seq = 0
        self._snapshot_pid: int | None = None

    def run(self) -> None:
        while not self._stop.wait(self._interval()):
            try:
                self.tick()
            except Exception as exc:
                # #204 broad-except audit: relay 循环的存活语义——tick 内已
                # 按族处理已知逃逸（传输/HTTP/解析），到达这里的是编程错误
                # 级意外；让异常杀死线程会让本机全部租约静默过期。吞并留痕，
                # 下一拍重试，真实死线是 Host 侧租约 TTL 与快照停滞停拍。
                self._log(f"心跳 relay 拍异常：{exc}")

    def _interval(self) -> float:
        try:
            config = self._get_config()
        except Exception:
            # #204 broad-except audit: 节拍间隔读取的容错——配置损坏/状态文件
            # 半写只影响本拍间隔，回落默认 15s；tick 内另有同族守卫兜底，
            # 让异常杀死 relay 线程会让本机全部租约静默过期。
            return 15.0
        return clamp_batch_interval(float(config.get("heartbeat_interval_seconds", 15) or 15))

    def tick(self) -> None:
        """One relay round; every skip path is silent-or-once and cheap."""
        try:
            host_url = str(self._get_config().get("host_url", ""))
        except Exception:
            # #204 broad-except audit: 同 _interval——配置读取失败 = 本拍
            # 无 host 可打，跳过即可；下一拍重读自愈。日志保全：配置损坏
            # 在 supervisor 控制台另有报错面（config 校验路径）。
            return
        if not host_url:
            return
        snapshot = read_snapshot(self._state_dir / SNAPSHOT_FILENAME)
        if snapshot is None or not _pid_alive(snapshot.get("pid")):
            return
        # PR #572 P2-3: an executor restart (new pid) re-probes the batch
        # endpoint — a Host upgrade must not wait out a supervisor restart.
        pid = int(snapshot["pid"])
        if pid != self._snapshot_pid:
            self._snapshot_pid, self._degraded = pid, False
        if snapshot_stale(snapshot, now=self._clock(), stale_seconds=SNAPSHOT_STALE_SECONDS):
            if not self._stale_logged:
                self._stale_logged = True
                self._log(
                    f"executor 租约快照停滞（{SNAPSHOT_STALE_SECONDS:.0f}s 未刷新），"
                    "暂停心跳 relay——租约将按 Host TTL 正常过期重排"
                )
            return
        self._stale_logged = False
        token = str(snapshot.get("token") or "")
        leases = [
            (str(pair[0]), str(pair[1]))
            for pair in snapshot["leases"]
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ]
        if not token or not leases:
            return
        if self._client is None or self._client_key != (host_url, token):
            self._client = self._make_client(host_url, token)
            self._client_key = (host_url, token)
        lost, cancelled = self._beat(leases)
        if lost is None:
            # Transient beat failure: the relay is still ALIVE — the liveness
            # write below must still happen (the executor's watchdog keys on
            # the advancing seq); the verdicts stay empty and the next tick
            # retries everything.
            lost, cancelled = [], []
        self._result_seq += 1
        write_beat_result(
            self._state_dir / RESULT_FILENAME,
            seq=self._result_seq,
            lost=lost,
            cancelled=cancelled,
        )

    def _beat(self, leases: list[tuple[str, str]]) -> tuple[Any, Any]:
        """Beat the whole snapshot; (None, None) = transient, retry next tick."""
        if self._degraded:
            return self._beat_singles(leases)
        outcome = self._beat_batch(leases)
        return (None, None) if outcome is None else outcome

    def _beat_batch(self, leases: list[tuple[str, str]]) -> tuple[list, list] | None:
        lost: list[tuple[str, str]] = []
        cancelled: list[str] = []
        for start in range(0, len(leases), MAX_BATCH_HEARTBEATS):
            chunk = leases[start : start + MAX_BATCH_HEARTBEATS]
            try:
                outcome = self._client.heartbeat_batch(chunk)
            except Exception as exc:
                # #204 broad-except audit: relay 的逐拍存活语义（与 executor
                # 内 batch loop 同族）：传输错误/非 200 的 RuntimeError/畸形
                # 应答都只丢这一拍，下一拍全量重来，Host 侧逐项谓词幂等；
                # 真正的死线是租约 TTL 与快照停滞停拍。401（token 已被重新
                # 注册轮换）额外丢弃缓存 client，下一份快照带新 token 重建。
                # 日志保全：每次失败都 log。
                self._log(f"心跳 relay 批量拍失败（{len(chunk)} 租约）：{exc}")
                if "HTTP 401" in str(exc):
                    self._client = None
                return None
            if outcome is None:
                self._degraded = True
                self._log("Host 无批量心跳端点，relay 降级为逐租约心跳")
                return self._beat_singles(leases)
            _status, body = outcome
            lost_ids = set(body.get("lost", []))
            lost.extend(pair for pair in chunk if pair[0] in lost_ids)
            cancelled.extend(body.get("cancelled_execution_ids", []))
        return lost, cancelled

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


def start_heartbeat_relay(store: Any, log: Callable[[str], None]) -> threading.Event:
    """Start the supervisor-lifetime relay thread; returns its stop event.

    ``store`` is the WorkerConfigStore (state dir + config reads); the relay
    no-ops until the executor publishes its first lease snapshot."""
    stop = threading.Event()
    relay = HeartbeatRelay(
        state_dir=store.state_dir,
        get_config=lambda: store.read(require_identity=False),
        stop=stop,
        log=log,
    )
    threading.Thread(target=relay.run, name="lease-heartbeat-relay", daemon=True).start()
    return stop
