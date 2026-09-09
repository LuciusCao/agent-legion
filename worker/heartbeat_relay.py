"""Supervisor-side lease heartbeat relay (#566 phase 2).

The batch lease heartbeat used to run as a daemon thread inside the
executor process — exactly the process whose GIL saturates under load, so
the beats starved while the machine still served work (phase 1's Host-side
deferral covers the gap; this relay removes it). The relay runs in the
supervisor process (idle by design): it reads the executor's lease
snapshot (``lease_snapshot.py``), beats those leases with the snapshot's
worker token (the beat transport lives in ``relay_beats.py``), and writes
the Host's verdicts (lost / cancelled) back to the beat-result file for
the executor to apply.

Safety rails:
- Only beats while the snapshot's pid is a live process AND the snapshot
  is fresh — a brain-dead executor's leases must expire on the normal Host
  TTL path, not be renewed forever from a frozen snapshot.
- 停拍 ≠ 失联（PR #572 codex P1）: a stale snapshot stops lease RENEWAL
  but the relay keeps a lightweight authenticated ping
  (``RelayBeater.control_plane_ping`` — no lease effect) so the Host's
  phase-1 deferral can still tell a starved executor from a dead worker;
  the 2×TTL hard bound owns the final reclaim.
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

from worker.execution.heartbeat_batch import clamp_batch_interval
from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_FILENAME,
    SNAPSHOT_STALE_SECONDS,
    read_snapshot,
    snapshot_stale,
    write_beat_result,
)
from worker.relay_beats import RelayBeater


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
        self._beater = RelayBeater(log=log, client_factory=client_factory)
        self._clock = clock
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
            self._snapshot_pid, self._beater.degraded = pid, False
        token = str(snapshot.get("token") or "")
        if snapshot_stale(snapshot, now=self._clock(), stale_seconds=SNAPSHOT_STALE_SECONDS):
            if not self._stale_logged:
                self._stale_logged = True
                self._log(
                    f"executor 租约快照停滞（{SNAPSHOT_STALE_SECONDS:.0f}s 未刷新），"
                    "租约停拍（Host 硬兜底回收）；控制面 ping 继续"
                )
            if token:
                self._beater.control_plane_ping(host_url, token)
            # 有意停拍也是存活：seq 照 advance，executor 看门狗不误报——
            # 「租约将过期」的信号面在上方停滞日志与 Host 侧 deferral。
            self._result_seq += 1
            write_beat_result(
                self._state_dir / RESULT_FILENAME, seq=self._result_seq, lost=[], cancelled=[]
            )
            return
        self._stale_logged = False
        leases = [
            (str(pair[0]), str(pair[1]))
            for pair in snapshot["leases"]
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ]
        if not token or not leases:
            return
        self._beater.ensure_client(host_url, token)
        lost, cancelled = self._beater.beat(leases)
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


def start_heartbeat_relay(
    store: Any, log: Callable[[str], None]
) -> tuple[threading.Event, threading.Thread]:
    """Start one relay thread; returns (stop event, thread) so the supervisor
    can terminate it on stop() and recreate it on the next start() (PR #572).

    ``store`` is the WorkerConfigStore (state dir + config reads); the relay
    no-ops until the executor publishes its first lease snapshot."""
    stop = threading.Event()
    relay = HeartbeatRelay(
        state_dir=store.state_dir,
        get_config=lambda: store.read(require_identity=False),
        stop=stop,
        log=log,
    )
    thread = threading.Thread(target=relay.run, name="lease-heartbeat-relay", daemon=True)
    thread.start()
    return stop, thread


def stop_heartbeat_relay(relay: tuple[threading.Event, threading.Thread] | None) -> None:
    """Terminate the relay thread (bounded join): a tick mid-flight finishes
    or the caller moves on — the thread is a daemon and exits at its next
    wait() boundary either way."""
    if relay is not None:
        relay[0].set()
        relay[1].join(timeout=5.0)
