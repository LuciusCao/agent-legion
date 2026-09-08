"""Emission side of the scheduler NOTIFY bridge (#521 方案 B).

Split from ``scheduler_notify.py`` for the file-size budget: the channel
constants and the LISTEN loop stay there; this module owns the emitters —
payload construction, the pooled-connection round-trip, and the
failure-escalation cadence. See the parent module's docstring for the
bridge's semantics (payload-free safety, poll-backoff fallback).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from server.app.db.connection import connect_database
from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.scheduler_notify import (
    NOTIFY_CHANNEL,
    PAYLOAD_JOB_TOUCHED_PREFIX,
    PAYLOAD_RESTOCK,
    PAYLOAD_SCAN_RELOAD,
)

logger = logging.getLogger(__name__)

_NOTIFY_SQL = f"notify {NOTIFY_CHANNEL}"

# Escalation cadence for emission failures: one INFO line per minute at
# most, DEBUG in between — a persistently degraded bridge (DB outage)
# stays visible during incident triage without per-notify spam.
_FAILURE_LOG_INTERVAL_SECONDS = 60.0
_last_failure_log = 0.0


def _emit_notify(dsn: ConnectSource, payload: str | None) -> None:
    """Best-effort cross-process ``NOTIFY`` on a pooled connection.

    The connection is a checkout from the shared pool — NOT autocommit,
    and a PostgreSQL NOTIFY only takes effect at its transaction's COMMIT:
    the pool's reset hook rolls back INTRANS returns, so the commit here
    is load-bearing (an uncommitted NOTIFY is silently dropped, not
    deferred).
    """
    global _last_failure_log
    try:
        sql = _NOTIFY_SQL if payload is None else f"notify {NOTIFY_CHANNEL}, '{payload}'"
        conn = connect_database(resolve_dsn(dsn))
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # #204 broad-except audit: fire-and-forget wake signal, never a
        # dependency of the write path that produced the work. The
        # failure space is the psycopg/pool surface of one pooled
        # checkout; the scheduler's 3s idle poll is the built-in fallback
        # latency, so the correct response to any failure here is drop
        # the wake, keep the write. Logging: DEBUG per failure (the
        # poll loop self-heals within one backoff interval), escalated
        # to INFO once per minute so a persistently degraded bridge
        # (DB outage) is visible during incident triage without
        # per-notify spam.
        now = time.monotonic()
        if now - _last_failure_log >= _FAILURE_LOG_INTERVAL_SECONDS:
            _last_failure_log = now
            logger.info(
                "cross-process scheduler notify has been failing "
                "(bridge degraded; scheduler poll backoff covers wakeups)",
                exc_info=True,
            )
        else:
            logger.debug("cross-process scheduler notify failed", exc_info=True)


def notify_schedulable_work_cross_process(dsn: ConnectSource) -> None:
    """Best-effort cross-process wake (plain "scan for schedulable work")."""
    _emit_notify(dsn, None)


def notify_scan_reload_cross_process(dsn: ConnectSource) -> None:
    """Best-effort "the scan list changed" notify (#521 方案 B).

    Emitted by the HTTP plane where the in-process scan-list reload is a
    no-op (no worker threads); the scheduler plane reloads its scan
    entries on receipt. Best-effort like the plain wake — a lost reload
    falls back to the scheduler restart (documented known limit).
    """
    _emit_notify(dsn, PAYLOAD_SCAN_RELOAD)


def bridge_scan_reload(request: Any) -> None:
    """Cross-plane scan-list reload for the http plane (#521 方案 B).

    Called by ``scheduler_wakeup.reload_worker_scan_entries`` when the
    app state has no workflow worker (http-plane process). Without this
    bridge a workspace created after the scheduler booted is never
    scanned — its jobs do not dispatch until a scheduler restart —
    because the scheduler's scan snapshot is loaded once at start and
    only the LISTEN listener reloads it afterwards.
    """
    dsn = getattr(getattr(request, "app", None), "state", None)
    dsn = getattr(dsn, "job_db", None)
    if dsn is not None:
        notify_scan_reload_cross_process(dsn)


def notify_job_touched_cross_process(dsn: ConnectSource, job_id: str) -> None:
    """Best-effort "this job changed state" event relay (#521 方案 B).

    Emitted by the scheduler plane for the job events it records (lease
    claims, finishes, expiries): its in-process event buffer is never
    drained by any SSE client there, so without this relay a job whose
    lifecycle is entirely scheduler-driven finishes with the dashboard
    not updating until page navigation. The http plane's listener folds
    the relayed job into ITS buffer (the one its clients drain), reading
    job facts from the DB — the payload only names the job.
    """
    # job_id is a server-generated UUID-ish identifier; the notify payload
    # is a single-quoted literal, so guard the quote character (defense in
    # depth — a hostile id can at worst mint a broken payload, which the
    # listener drops on its own parse).
    safe_id = job_id.replace("'", "")
    _emit_notify(dsn, f"{PAYLOAD_JOB_TOUCHED_PREFIX}{safe_id}")


def notify_restock_cross_process(dsn: ConnectSource) -> None:
    """Best-effort "empty claim observed, restock now" notify (#521 N2).

    The http plane's debounced empty-claim trigger fires this instead of a
    plain wake: the scheduler plane expires its agent-stock snapshot
    (force refresh) before waking, restoring the combined role's
    request_restock semantics (force_refresh + wake). Without the payload
    the snapshot stays frozen for up to agent_stock.refresh_seconds (30s
    default), delaying burst recovery by that window.
    """
    _emit_notify(dsn, PAYLOAD_RESTOCK)
