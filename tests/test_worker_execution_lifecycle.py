"""Unit tests for worker heartbeat liveness (worker/execution_lifecycle.py)."""

from __future__ import annotations

import subprocess
import sys
import threading

from worker.execution_lifecycle import HeartbeatConfig, heartbeat_loop


class FakeClient:
    def __init__(self) -> None:
        self.heartbeats = 0

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        return 204, []


def _exited_process() -> subprocess.Popen[bytes]:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc


def _start_loop(
    client: FakeClient,
    stop: threading.Event,
    proc_ref: dict[str, subprocess.Popen[bytes] | None],
    adopted: threading.Event,
) -> threading.Thread:
    config = HeartbeatConfig(
        client=client,
        execution_id="exec-1",
        lease_id="lease-1",
        stop=stop,
        interval=0.02,
        ownership_lost=threading.Event(),
        proc_ref=proc_ref,
        adopted=adopted,
    )
    thread = threading.Thread(target=heartbeat_loop, args=(config,), daemon=True)
    thread.start()
    return thread


def test_heartbeat_stops_when_process_exited_and_not_adopted() -> None:
    client = FakeClient()
    thread = _start_loop(client, threading.Event(), {"proc": _exited_process()}, threading.Event())
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert client.heartbeats == 0


def test_heartbeat_keeps_beating_when_adopted_after_process_exit() -> None:
    client = FakeClient()
    stop = threading.Event()
    adopted = threading.Event()
    adopted.set()
    thread = _start_loop(client, stop, {"proc": _exited_process()}, adopted)
    thread.join(timeout=0.2)
    assert thread.is_alive()
    assert client.heartbeats >= 1
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_heartbeat_keeps_beating_while_process_alive() -> None:
    client = FakeClient()
    stop = threading.Event()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        thread = _start_loop(client, stop, {"proc": proc}, threading.Event())
        thread.join(timeout=0.2)
        assert thread.is_alive()
        assert client.heartbeats >= 1
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        proc.kill()
        proc.wait()
