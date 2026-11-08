"""Checkout attribution and leak-proof pool return for database connections."""

from __future__ import annotations

import logging
import sys
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from server.app.db.pool_reset import record_checkout_origin, settle_return

logger = logging.getLogger(__name__)


def stamp_checkout_origin(raw: Connection[dict[str, Any]]) -> None:
    # Bounded frame walk, no formatting until a leak actually hits the reset hook.
    record_checkout_origin(raw, sys._getframe())


def return_checkout(
    raw: Connection[dict[str, Any]],
    pool: ConnectionPool[Connection[dict[str, Any]]],
) -> None:
    # #438: observe BEFORE the pool's async reset — this is the only
    # synchronous point where a dirty return can be attributed to its
    # checkout site. #1077: the dirty transaction is also rolled back
    # here, so it never depends on the pool worker's ReturnConnection
    # (discarded when a pool close wins the race).
    # #439: the return must never be blocked — if settle_return raises
    # (broken connection, unexpected info error) the checkout would
    # leak all over again. Its failure loses the observation/rollback
    # only, not the connection.
    try:
        settle_return(raw)
    except Exception:
        # #204 broad-except audit: the return path must not gain a leak
        # path (#439). settle_return's failures are a broken connection
        # or an unexpected info error (psycopg rollback errors are
        # already absorbed inside) — un-narrowable here, and any raise
        # would skip putconn and strand the checkout all over again.
        # Losing the observation is strictly cheaper than losing the
        # return; the pool reset hook is the independent backstop.
        logger.debug("settle_return failed during close (#439)", exc_info=True)
    pool.putconn(raw)
