"""Live-path tests for the host role split (#521 方案 B).

Runs against the real per-worktree test database and pins the two
cross-process invariants the unit fakes cannot see:

- the NOTIFY bridge actually round-trips (an emission on one connection
  is delivered to a listener on another);
- the per-plane probe locks are disjoint (an http-plane holder does not
  block a scheduler-plane holder on the same database, while a second
  holder of the SAME plane is detected).

The scheduler process itself is not spawned here — its composition is
`run_scheduler_process`, which shares the production code paths already
covered (worker_startup threads, probe, listener); spawning a real
second process against the shared test database would fight xdist
siblings for the schema.
"""

from __future__ import annotations

import threading
import time

from server.app.scheduler_notify import (
    SchedulerNotifyListener,
    notify_schedulable_work_cross_process,
)
from server.app.scheduler_wakeup import register_wakeup, unregister_wakeup
from server.app.single_replica_probe import SingleReplicaProbe
from tests.postgres_support import TEST_DATABASE_URL


def test_notify_bridge_round_trips() -> None:
    listener = SchedulerNotifyListener(TEST_DATABASE_URL)
    listener._POLL_INTERVAL_SECONDS = 1.0
    delivered = threading.Event()
    # The listener maps a delivered NOTIFY to a local wakeup dispatch, so
    # a registered callback is the observable end of the bridge.
    register_wakeup(delivered.set)
    listener.start()
    try:
        # The LISTEN registration needs a moment to take effect server-side.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not delivered.is_set():
            notify_schedulable_work_cross_process(TEST_DATABASE_URL)
            delivered.wait(timeout=0.5)
        assert delivered.is_set(), "NOTIFY was not delivered to the listener"
    finally:
        unregister_wakeup(delivered.set)
        listener.stop()


def test_probe_locks_are_disjoint_per_plane() -> None:
    http_probe = SingleReplicaProbe(TEST_DATABASE_URL, lock_name="control-plane-http")
    scheduler_probe = SingleReplicaProbe(TEST_DATABASE_URL, lock_name="scheduler")
    http_probe_same_plane = SingleReplicaProbe(TEST_DATABASE_URL, lock_name="control-plane-http")
    try:
        assert http_probe.probe() is True
        # The other plane does not contend with the http plane's lock.
        assert scheduler_probe.probe() is True
        # A second process of the SAME plane is the detected hazard.
        assert http_probe_same_plane.probe() is False
        assert http_probe_same_plane.lock_acquired is False
    finally:
        http_probe.close()
        scheduler_probe.close()
        http_probe_same_plane.close()
