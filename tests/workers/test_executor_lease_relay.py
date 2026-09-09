"""Executor-side lease-relay wiring (#566 phase 2): snapshot mode.

With ``AGENT_WORKER_LEASE_SNAPSHOT`` set (the supervisor-spawned shape) the
executor runs no in-process beat thread: it publishes the beatable lease
snapshot from the claim loop and applies the relay's beat results (lost →
ownership_lost, cancelled → cancel callbacks).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from tests.workers.helpers import FakeClient, _claim, _run_main
from worker import executor as agent_worker
from worker.execution import run as execution_run
from worker.lease_snapshot import (
    RESULT_FILENAME,
    SNAPSHOT_ENV_VAR,
    SNAPSHOT_FILENAME,
    read_snapshot,
    write_beat_result,
)

pytestmark = pytest.mark.no_db


def test_snapshot_mode_publishes_leases_and_applies_beat_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SNAPSHOT_ENV_VAR, str(tmp_path / SNAPSHOT_FILENAME))
    fake = FakeClient(tmp_path / "unused.tar.gz")
    fake.token = "worker-token"  # type: ignore[attr-defined]
    first_claimed = threading.Event()
    release = threading.Event()

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        if first_claimed.is_set():
            return None
        first_claimed.set()
        return _claim("exec-1")

    fake.claim = claim  # type: ignore[attr-defined]
    ownership_events: dict[str, threading.Event] = {}
    cancelled_calls: list[list[str]] = []

    def blocking_execution(*args):  # type: ignore[no-untyped-def]
        # run_execution positional tail: (..., download_slots, heartbeat_registry).
        registry = args[10]
        claimed = args[1]
        ownership_lost = threading.Event()
        registry.register(
            claimed["execution_id"],
            claimed["lease_id"],
            ownership_lost,
            on_cancelled=lambda ids: cancelled_calls.append(list(ids)),
        )
        ownership_events[claimed["execution_id"]] = ownership_lost
        release.wait(timeout=10)

    monkeypatch.setattr(execution_run, "run_execution", blocking_execution)
    thread, handlers, _result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
    try:
        assert first_claimed.wait(timeout=5), "first claim never happened"

        deadline = time.monotonic() + 8
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = read_snapshot(tmp_path / SNAPSHOT_FILENAME)
            if snapshot is not None and snapshot["leases"]:
                break
            time.sleep(0.05)
        assert snapshot is not None, "snapshot never published"
        assert snapshot["leases"] == [["exec-1", "lease-1"]]
        assert snapshot["token"] == "worker-token"
        assert snapshot["pid"] == os.getpid()

        # The supervisor relay's verdicts flow back through the result file.
        write_beat_result(
            tmp_path / RESULT_FILENAME,
            seq=1,
            lost=[("exec-1", "lease-1")],
            cancelled=["exec-1"],
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (
            ownership_events["exec-1"].is_set() and cancelled_calls
        ):
            time.sleep(0.05)
        assert ownership_events["exec-1"].is_set(), "lost verdict never applied"
        assert cancelled_calls == [["exec-1"]]
    finally:
        handlers[agent_worker.signal.SIGTERM]()
        release.set()
        thread.join(timeout=10)
