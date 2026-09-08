"""Cross-process scheduler wakeup over PostgreSQL LISTEN/NOTIFY (#521 方案 B).

The role split moves the workflow scheduler into a dedicated process; the
write paths that produce newly schedulable work (run intake, publish,
approval decisions, ...) run on the HTTP plane, so the process-local
``scheduler_wakeup.notify_schedulable_work`` registry alone can no longer
reach the scheduler. This module is the bridge:

- HTTP-plane processes emit ``NOTIFY agent_legion_schedulable`` on a
  pooled autocommit connection (one round-trip, fire-and-forget — the
  NOTIFY takes effect at commit, and autocommit commits immediately);
- the scheduler process runs one LISTEN loop thread that turns each
  notification into a local ``notify_schedulable_work()`` call, waking
  the workflow worker's poll loop immediately instead of waiting out its
  idle backoff.

Payload-free by design: notifications only mean "scan for schedulable
work", never "execute this". Lost notifications are safe — the scheduler
polls on a 3s idle backoff regardless; the bridge buys latency, not
correctness. Duplicate notifications are likewise safe (a woken poll
that finds no work simply backs off again).

Failure semantics mirror the in-process registry: emission is best-effort
(a DB hiccup logs at debug and the write path continues — the poll
backoff is the fallback latency).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from server.app.db.connection import connect_database
from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.scheduler_wakeup import notify_schedulable_work

logger = logging.getLogger(__name__)

# Channel identity mirrors the advisory-lock naming family
# (single_replica_probe): a fixed, deployment-scoped literal.
NOTIFY_CHANNEL = "agent_legion_schedulable"

_LISTEN_SQL = f"listen {NOTIFY_CHANNEL}"
_NOTIFY_SQL = f"notify {NOTIFY_CHANNEL}"


def notify_schedulable_work_cross_process(dsn: ConnectSource) -> None:
    """Best-effort cross-process wake: ``NOTIFY`` on a pooled connection.

    Called by the HTTP plane's wakeup dispatch (scheduler_wakeup). The
    connection is a checkout from the shared pool — NOT autocommit, and
    a PostgreSQL NOTIFY only takes effect at its transaction's COMMIT:
    the pool's reset hook rolls back INTRANS returns, so the commit here
    is load-bearing (an uncommitted NOTIFY is silently dropped, not
    deferred).
    """
    try:
        conn = connect_database(resolve_dsn(dsn))
        try:
            conn.execute(_NOTIFY_SQL)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # #204 broad-except audit: fire-and-forget wake signal, never a
        # dependency of the write path that produced schedulable work.
        # The failure space is the psycopg/pool surface of one pooled
        # checkout; the scheduler's 3s idle poll is the built-in fallback
        # latency, so the correct response to any failure here is drop
        # the wake, keep the write. Debug level: transient by nature, the
        # poll loop self-heals within one backoff interval.
        logger.debug("cross-process scheduler notify failed", exc_info=True)


class SchedulerNotifyListener:
    """Dedicated-connection LISTEN loop turning NOTIFY into local wakeups.

    One instance per scheduler-plane process. The listener holds its own
    psycopg connection — NOT a pool checkout: the pool's idle recycling
    would reclaim the connection and silently drop the LISTEN
    registration, and the DB-API facade (DatabaseConnection) does not
    expose the notification generator. The connection is autocommit so
    the LISTEN takes effect immediately; the thread blocks in the
    ``notifies()`` generator and maps each delivered notification to one
    local ``scheduler_wakeup.notify_schedulable_work()`` call.
    """

    _POLL_INTERVAL_SECONDS = 5.0

    def __init__(self, dsn: ConnectSource) -> None:
        self._dsn = resolve_dsn(dsn)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the consumer thread; never raises.

        Startup contract mirrors the sweeper/workflow-worker threads
        (worker_startup.py): a failure is logged, not raised — the
        scheduler keeps running on its poll backoff without the bridge.
        Connection/relisten happens inside the loop so a DB outage at
        boot degrades to retry-with-backoff instead of a dead thread.
        """
        self._thread = threading.Thread(
            target=self._loop, name="scheduler-notify-listener", daemon=True
        )
        self._thread.start()

    def _connect(self) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn, autocommit=True)
        # Server timezone parity with the pooled connections (rows.py's
        # configure_connection minus the commit — autocommit here).
        conn.execute("set timezone = 'UTC'")
        return conn

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn = self._connect()
                try:
                    conn.execute(_LISTEN_SQL)
                    for _notify in conn.notifies(timeout=self._POLL_INTERVAL_SECONDS):
                        notify_schedulable_work()
                finally:
                    conn.close()
            except Exception:
                # #204 broad-except audit: the loop's life support — same
                # discipline as the sweeper/intake loops. This thread is
                # the only NOTIFY consumer of the scheduler process;
                # dying would silently regress intake→dispatch latency to
                # the poll backoff for the process lifetime. The outcome
                # space is the psycopg connect/LISTEN/notification surface
                # (transient DB outages, broken sockets, server restarts);
                # log-and-retry with the interval as backoff is the
                # containment, and the scheduler's own poll backoff keeps
                # scheduling correct throughout.
                logger.exception("scheduler notify listener loop failed")
                self._stop_event.wait(self._POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the loop to stop; best-effort and idempotent.

        The loop notices the stop event within one poll interval and
        closes its own connection; ``join`` bounds the wait for callers
        tearing the process down.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._POLL_INTERVAL_SECONDS)
