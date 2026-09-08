"""Cross-process scheduler bridge over PostgreSQL LISTEN/NOTIFY (#521 方案 B).

The role split moves the workflow scheduler into a dedicated process; the
write paths that produce newly schedulable work (run intake, publish,
approval decisions, ...) run on the HTTP plane, so the process-local
``scheduler_wakeup.notify_schedulable_work`` registry alone can no longer
reach the scheduler. This module is the bridge:

- plane processes emit ``NOTIFY agent_legion_schedulable`` (with an
  optional payload, see below) — the emitters live in the sister module
  ``scheduler_notify_emit.py``;
- the scheduler process runs one LISTEN loop thread (below) that turns
  each notification into local scheduler-plane actions, waking the
  workflow worker's poll loop immediately instead of waiting out its
  idle backoff;
- symmetrically, the scheduler plane emits ``job_touched`` events for the
  job events it records (lease claims, finishes, expiries — the http
  plane's SSE clients never see the scheduler's in-process buffer), and
  the http plane runs its own listener that folds them into ITS buffer.

Payloads name the trigger kind; unknown payloads fall back to the plain
wake (forward compatibility):

- ``''`` / ``'schedulable'`` — "scan for schedulable work" (the default
  wakeup notify);
- ``'scan_reload'`` — "the workspace scan list changed" (workspace
  created/re-keyed/first-published on the HTTP plane): the scheduler
  plane reloads its scan entries before the wake, so a newly created
  workspace is scheduled without a scheduler restart. This closes the
  cross-plane gap the in-process ``reload_worker_scan_entries`` helper
  cannot (it reads ``app.state.workflow_worker``, which only exists in
  the process that started the worker threads);
- ``'job_touched:<job_id>'`` — "this job changed state on the emitting
  plane": the listening plane records the job into its own event buffer
  so its SSE clients refresh. The only payload carrying an argument, and
  deliberately an opaque id: the receiver re-reads job facts from the
  DB, the notification never carries state.

Notifications only ever mean "look at the database" — they never carry
work items, so they are safe to lose (the scheduler polls on a 3s idle
backoff regardless; the bridge buys latency, not correctness) and safe
to duplicate (a woken poll that finds no work simply backs off again).

Failure semantics mirror the in-process registry: emission is best-effort
(a DB hiccup logs and the write path continues — the poll backoff is the
fallback latency).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.scheduler_wakeup import notify_local_wakeups

logger = logging.getLogger(__name__)

# Channel identity mirrors the advisory-lock naming family
# (single_replica_probe): a fixed, deployment-scoped literal.
NOTIFY_CHANNEL = "agent_legion_schedulable"

# Payload kinds (forward-compatible: unknown payloads fall back to wake).
PAYLOAD_SCHEDULABLE = "schedulable"
PAYLOAD_SCAN_RELOAD = "scan_reload"
PAYLOAD_RESTOCK = "restock"
# Prefix for the argument-carrying job-event relay (payload is
# ``job_touched:<job_id>``).
PAYLOAD_JOB_TOUCHED_PREFIX = "job_touched:"

_LISTEN_SQL = f"listen {NOTIFY_CHANNEL}"


class SchedulerNotifyListener:
    """Dedicated-connection LISTEN loop turning NOTIFY into plane actions.

    One instance per bridged process (the scheduler plane for scheduler
    wakeups, the http plane for job_touched events). The listener holds
    its own psycopg connection — NOT a pool checkout: the pool's idle
    recycling would reclaim the connection and silently drop the LISTEN
    registration, and the DB-API facade (DatabaseConnection) does not
    expose the notification generator. The connection is autocommit so
    the LISTEN takes effect immediately.

    The loop re-enters the ``notifies()`` generator per timeout slice
    instead of holding one long-lived generator: psycopg's generator
    ENDS when the timeout expires, so the naive "for over one
    generator" shape would close and reconnect every slice (~60
    connections/minute on an idle deployment plus recurring miss
    windows during each reconnect).
    """

    _POLL_INTERVAL_SECONDS = 5.0
    _SLICE_SECONDS = 1.0

    def __init__(
        self,
        dsn: ConnectSource,
        on_scan_reload: Any | None = None,
        on_job_touched: Any | None = None,
        on_restock: Any | None = None,
    ) -> None:
        self._dsn = resolve_dsn(dsn)
        self._on_scan_reload = on_scan_reload
        self._on_job_touched = on_job_touched
        self._on_restock = on_restock
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the consumer thread; never raises.

        Startup contract mirrors the sweeper/workflow-worker threads
        (worker_startup.py): a failure is logged, not raised — the
        process keeps running on its poll backoff without the bridge.
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

    def _dispatch(self, payload: str) -> None:
        if payload.startswith(PAYLOAD_JOB_TOUCHED_PREFIX):
            # Consumed only by the plane that wired on_job_touched (the
            # http plane): the scheduler process listens on the same
            # channel it emits job_touched to, so without this early
            # return every scheduler-side event would fall through to the
            # plain wake below and add a spurious poll wakeup per event.
            job_id = payload[len(PAYLOAD_JOB_TOUCHED_PREFIX) :]
            if job_id and self._on_job_touched is not None:
                try:
                    self._on_job_touched(job_id)
                except Exception:
                    # #204 broad-except audit: per-callback containment on
                    # the event relay (same contract as scheduler_wakeup's
                    # callback loop) — a failing receiver must not kill the
                    # listener thread; the next state change re-records.
                    logger.exception("job_touched relay callback failed")
            return
        if payload == PAYLOAD_RESTOCK and self._on_restock is not None:
            try:
                self._on_restock()
            except Exception:
                # #204 broad-except audit: per-callback containment, same
                # contract as the reload branch below — the stock gate's
                # TTL refresh is the fallback, a failed forced refresh must
                # not kill the listener thread.
                logger.exception("restock callback failed")
            notify_local_wakeups()
            return
        if payload == PAYLOAD_SCAN_RELOAD and self._on_scan_reload is not None:
            try:
                self._on_scan_reload()
            except Exception:
                # #204 broad-except audit: the reload callback is the
                # workflow worker's reload_scan_entries (DB read + swap);
                # per-callback containment matches scheduler_wakeup's
                # contract — a failing reload must not kill the listener
                # thread (that would regress every later notification to
                # the poll backoff). The worker keeps its previous scan
                # snapshot; the next reload or restart converges.
                logger.exception("scan-list reload callback failed")
        notify_local_wakeups()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn = self._connect()
                try:
                    conn.execute(_LISTEN_SQL)
                    while not self._stop_event.is_set():
                        # The generator ends when the slice times out;
                        # re-entering it on the SAME connection keeps the
                        # LISTEN registration alive without reconnects.
                        for notification in conn.notifies(timeout=self._SLICE_SECONDS):
                            self._dispatch(str(notification.payload))
                finally:
                    conn.close()
            except Exception:
                # #204 broad-except audit: the loop's life support — same
                # discipline as the sweeper/intake loops. This thread is
                # the only NOTIFY consumer of the process for its plane;
                # dying would silently regress cross-plane latency to the
                # poll backoff for the process lifetime. The outcome
                # space is the psycopg connect/LISTEN/notification surface
                # (transient DB outages, broken sockets, server restarts);
                # log-and-retry with the interval as backoff is the
                # containment, and the poll backoff keeps the system
                # correct throughout.
                logger.exception("scheduler notify listener loop failed")
                self._stop_event.wait(self._POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the loop to stop; best-effort and idempotent.

        The loop notices the stop event within one slice and closes its
        own connection; ``join`` bounds the wait for callers tearing the
        process down.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._POLL_INTERVAL_SECONDS)
