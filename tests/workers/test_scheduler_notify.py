"""Cross-process scheduler wakeup bridge (#521 方案 B).

Unit tests with fake connections: the SQL shapes (NOTIFY emission with
its load-bearing commit, LISTEN registration), the best-effort failure
containment, and the listener→local-wakeup fan-out. The listener's
connection is injected via ``_connect`` (the production path opens a raw
psycopg connection, which no fake can stand in for); the live
PostgreSQL round-trip is covered by tests/db/test_role_split_postgres.py.
"""

from __future__ import annotations

import threading

import pytest

from server.app import scheduler_notify_emit, scheduler_wakeup
from server.app.scheduler_notify import (
    NOTIFY_CHANNEL,
    SchedulerNotifyListener,
)
from server.app.scheduler_notify_emit import (
    notify_schedulable_work_cross_process,
)

pytestmark = pytest.mark.no_db

_DSN = "postgresql://127.0.0.1:5432/agent_legion_notify_test"


class FakeResult:
    def fetchone(self) -> dict[str, bool]:
        return {"acquired": True}


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.commits = 0
        self.closed = False

    def execute(self, sql: str, params: object = None) -> FakeResult:
        self.executed.append(sql)
        return FakeResult()

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True

    def notifies(self, timeout: float | None = None):
        """Overridden per-test via instance attribute when needed."""
        return iter(())
        yield  # pragma: no cover - makes this a generator


def test_notify_emits_channel_sql_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConnection()
    monkeypatch.setattr(scheduler_notify_emit, "connect_database", lambda dsn: fake)

    notify_schedulable_work_cross_process(_DSN)

    assert fake.executed == [f"notify {NOTIFY_CHANNEL}"]
    # A pooled connection is not autocommit and the pool's reset rolls
    # back INTRANS returns — without this commit the NOTIFY is dropped.
    assert fake.commits == 1
    assert fake.closed is True


def test_notify_never_raises_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(_dsn):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(scheduler_notify_emit, "connect_database", _fail)

    # Must not raise — the scheduler poll backoff is the fallback latency.
    notify_schedulable_work_cross_process(_DSN)


def test_wakeup_dispatch_invokes_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(scheduler_wakeup, "_notify_backend", lambda: calls.append("backend"))

    scheduler_wakeup.notify_schedulable_work()

    assert calls == ["backend"]


def test_wakeup_backend_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise RuntimeError("backend broke its contract")

    monkeypatch.setattr(scheduler_wakeup, "_notify_backend", _boom)
    local_called: list[bool] = []
    monkeypatch.setattr(scheduler_wakeup, "_callbacks", [lambda: local_called.append(True)])

    scheduler_wakeup.notify_schedulable_work()

    assert local_called == [True]


def test_listener_maps_notifications_to_local_wakeups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeConnection()
    notified = threading.Event()

    class _FakeNotify:
        def __init__(self) -> None:
            self.payload = ""

        def __str__(self) -> str:
            return "notify"

    def _notifies(timeout: float | None = None):
        for _ in range(2):
            notified.wait(timeout=5)
            notified.clear()
            n = _FakeNotify()
            n.payload = "schedulable"
            yield n

    monkeypatch.setattr(fake, "notifies", _notifies)
    monkeypatch.setattr(SchedulerNotifyListener, "_connect", lambda self: fake)
    wakeups: list[int] = []
    monkeypatch.setattr(scheduler_wakeup, "_callbacks", [lambda: wakeups.append(1)])

    listener = SchedulerNotifyListener(_DSN)
    listener._SLICE_SECONDS = 0.05
    listener.start()
    try:
        for expected in (1, 2):
            notified.set()
            # A short grace period lets the loop drain the notification.
            for _ in range(100):
                if len(wakeups) >= expected:
                    break
                listener._stop_event.wait(0.02)
        assert len(wakeups) == 2
    finally:
        listener.stop()
    # One connection, one LISTEN — the loop re-enters the generator per
    # slice instead of reconnecting (the churn fix on review).
    assert fake.executed == [f"listen {NOTIFY_CHANNEL}"]


def test_listener_reloads_scan_list_on_scan_reload_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeConnection()
    notified = threading.Event()
    reloads: list[int] = []

    class _FakeNotify:
        payload = "scan_reload"

    def _notifies(timeout: float | None = None):
        notified.wait(timeout=5)
        notified.clear()
        yield _FakeNotify()

    monkeypatch.setattr(fake, "notifies", _notifies)
    monkeypatch.setattr(SchedulerNotifyListener, "_connect", lambda self: fake)
    wakeups: list[int] = []
    monkeypatch.setattr(scheduler_wakeup, "_callbacks", [lambda: wakeups.append(1)])

    listener = SchedulerNotifyListener(_DSN, on_scan_reload=lambda: reloads.append(1))
    listener._SLICE_SECONDS = 0.05
    listener.start()
    try:
        notified.set()
        for _ in range(100):
            if len(wakeups) >= 1 and len(reloads) >= 1:
                break
            listener._stop_event.wait(0.02)
        # scan_reload runs the reload callback AND wakes the poll loop.
        assert reloads == [1]
        assert wakeups == [1]
    finally:
        listener.stop()


def test_listener_scan_reload_callback_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeConnection()
    notified = threading.Event()

    class _FakeNotify:
        payload = "scan_reload"

    def _notifies(timeout: float | None = None):
        notified.wait(timeout=5)
        notified.clear()
        yield _FakeNotify()

    def _boom() -> None:
        raise RuntimeError("reload broke")

    monkeypatch.setattr(fake, "notifies", _notifies)
    monkeypatch.setattr(SchedulerNotifyListener, "_connect", lambda self: fake)
    wakeups: list[int] = []
    monkeypatch.setattr(scheduler_wakeup, "_callbacks", [lambda: wakeups.append(1)])

    listener = SchedulerNotifyListener(_DSN, on_scan_reload=_boom)
    listener._SLICE_SECONDS = 0.05
    listener.start()
    try:
        notified.set()
        for _ in range(100):
            if len(wakeups) >= 1:
                break
            listener._stop_event.wait(0.02)
        # The wake still fires even when the reload callback fails.
        assert wakeups == [1]
    finally:
        listener.stop()


def test_reload_worker_scan_entries_bridges_when_no_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#521 方案 B: on the http plane (no app-state worker) the scan-list
    reload crosses the NOTIFY bridge instead of silently no-op'ing. The
    production path lazy-imports the emitter from scheduler_notify_emit,
    patching the source module's attribute covers the call."""
    from types import SimpleNamespace

    from server.app.scheduler_wakeup import reload_worker_scan_entries

    emitted: list[str] = []
    monkeypatch.setattr(
        scheduler_notify_emit,
        "notify_scan_reload_cross_process",
        lambda dsn: emitted.append("scan_reload"),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_db=_DSN)))

    reload_worker_scan_entries(request)

    assert emitted == ["scan_reload"]


def test_listener_survives_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    state = {"failures": 0}

    def _connect(self):
        attempts.append(1)
        if state["failures"] < 2:
            state["failures"] += 1
            raise RuntimeError("database unavailable")
        return FakeConnection()

    monkeypatch.setattr(SchedulerNotifyListener, "_connect", _connect)

    listener = SchedulerNotifyListener(_DSN)
    listener._POLL_INTERVAL_SECONDS = 0.02
    listener.start()
    try:
        for _ in range(200):
            if len(attempts) >= 3:
                break
            listener._stop_event.wait(0.02)
        assert len(attempts) >= 3  # two failures + one successful connect
    finally:
        listener.stop()
