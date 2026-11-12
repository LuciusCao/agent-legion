"""Pool return-path leak telemetry for pooled connections (#438).

Lives in its own module because the wiring is bidirectional:
``connection.py`` stamps the checkout origin on every raw connection and
``pools.py`` installs ``reset_connection`` as the pool's ``reset``
callback — importing either from the other would close an import cycle.

The pool is the last line of defense against dirty returns: psycopg_pool
rolls back INTRANS/INERROR connections on ``putconn`` (asynchronously, on
its maintenance workers). What the stock behavior lacks is attribution —
the rollback logs an anonymous connection repr, so #438's long-lived
idle-in-transaction sightings could not be traced to a call site. This
module adds that attribution, and (#1077) rolls a dirty return back on the
caller's thread so the transaction never outlives a discarded pool task.
"""

from __future__ import annotations

import contextlib
import logging
import time
from threading import Lock
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus

logger = logging.getLogger(__name__)

# One warning line per leak signature per interval: production saw the
# underlying WARNING storm at a high rate, so the diagnostic must be
# deduplicated per checkout-site signature and rate-limited — a systemic
# leak must surface as a steady, greppable trickle, not join the storm.
_RESET_WARN_EVERY_SECONDS = 60.0
_MAX_ORIGIN_FRAMES = 5
# Hard cap on the frame walk (#439): the collected-frames bound above does
# not bound the walk itself — a checkout buried under deep plumbing (or a
# stack with no business frames at all) would traverse the entire call
# stack. Give up past this many walked frames and mark the origin unknown.
_MAX_ORIGIN_WALK = 30
# Skip the connection plumbing itself when attributing a leak: this
# package's helpers, the JobQueries facade methods, contextlib's
# @contextmanager machinery, psycopg, and tests.
_ORIGIN_SKIP_MODULES = (
    "server.app.db.",
    "server.app.jobs.queries.",
    "contextlib",
    "psycopg",
    "tests.",
)

_reset_warn_lock = Lock()
_reset_warn_last: dict[str, float] = {}
# Suppressed-hit counts per signature (#439): rate-limiting must not erase
# frequency — the next emitted warning carries how many hits it swallowed,
# so "one leak" and "leaking every second for a minute" stay distinguishable.
_reset_warn_suppressed: dict[str, int] = {}


def settle_return(conn: Connection[dict[str, Any]]) -> None:
    """Attribute, then roll back, a dirty return (``DatabaseConnection.close``).

    Called synchronously on the caller's thread, before psycopg_pool's
    maintenance workers get the connection — so unlike the ``reset``
    callback this sees the pre-rollback transaction status and can
    attribute the leak while it is still a leak.

    #1077: the rollback happens here too, not only on the pool worker. The
    worker's ``ReturnConnection`` is discarded when ``ConnectionPool.close()``
    flags the pool closed before the task runs, and no shutdown barrier can
    cover a return that lands after it (a DB call still running past the
    lifespan's bounded joins). Rolling back on the caller's thread means a
    dirty transaction never depends on that task: a discarded task can at
    most strand an IDLE connection (no snapshot, no locks) until GC. A
    failed rollback (broken connection) leaves the pool's reset path as the
    backstop — psycopg_pool discards connections it cannot reset.
    """
    status = conn.info.transaction_status
    if status == TransactionStatus.IDLE:
        return
    _warn_reset_leak(status, conn)
    if status in (TransactionStatus.INTRANS, TransactionStatus.INERROR):
        with contextlib.suppress(psycopg.Error):
            conn.rollback()


def reset_connection(conn: Connection[dict[str, Any]]) -> None:
    """Pool ``reset`` callback: verify the returned connection is IDLE.

    By the time this runs, psycopg_pool's return path has already rolled
    back any open transaction, so a connection that is STILL non-IDLE here
    means either the rollback failed or someone reopened a transaction in
    the gap — both worth one attributed warning. The hook must not raise:
    psycopg_pool discards the connection if the hook errors, which is fine,
    but a clean return keeps the fast path cheap. The connection is left
    untouched; psycopg_pool verifies IDLE-ness after the hook itself and
    discards offenders.
    """
    status = conn.info.transaction_status
    if status != TransactionStatus.IDLE:
        _warn_reset_leak(status, conn)


def _warn_reset_leak(status: TransactionStatus, conn: Connection[dict[str, Any]]) -> None:
    origin = getattr(conn, "agent_legion_origin", "unknown (no checkout origin recorded)")
    key = f"{status.name}:{origin}"
    now = time.monotonic()
    with _reset_warn_lock:
        last = _reset_warn_last.get(key)
        if last is not None and now - last < _RESET_WARN_EVERY_SECONDS:
            _reset_warn_suppressed[key] = _reset_warn_suppressed.get(key, 0) + 1
            return
        _reset_warn_last[key] = now
        suppressed = _reset_warn_suppressed.pop(key, 0)
    logger.warning(
        "db connection returned %s with an open transaction (#438): rolled back on "
        "return. leak origin: %s%s",
        status.name,
        origin,
        f" (suppressed {suppressed} hits in the last interval)" if suppressed else "",
    )


def record_checkout_origin(conn: Connection[dict[str, Any]], frame: Any) -> None:
    """Stamp the checkout call path on the connection for leak attribution."""
    conn.agent_legion_origin = _checkout_origin(frame)  # type: ignore[attr-defined]


def _checkout_origin(frame: Any) -> str:
    """Compact caller-path signature for leak attribution.

    Walks outward from the checkout, skipping connection plumbing frames;
    the first frames that remain are the business call site that borrowed
    the connection. Bounded twice over (#439): at most 5 collected frames,
    and at most 30 walked frames total — the collected-frames bound alone
    did not bound the walk, because skipped plumbing frames never count
    toward it, so a deep stack (or one with no business frames at all)
    walked the entire call chain.
    """
    names: list[str] = []
    walked = 0
    while frame is not None and len(names) < _MAX_ORIGIN_FRAMES and walked < _MAX_ORIGIN_WALK:
        walked += 1
        module = frame.f_globals.get("__name__", "?")
        if not module.startswith(_ORIGIN_SKIP_MODULES):
            names.append(f"{module.rsplit('.', 1)[-1]}.{frame.f_code.co_name}")
        frame = frame.f_back
    if not names and walked >= _MAX_ORIGIN_WALK:
        return "unknown (walk cap hit: no non-plumbing frame within 30)"
    return "/".join(names) or "unknown"
