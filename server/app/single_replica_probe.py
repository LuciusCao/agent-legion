"""Second-replica detection via a session-scoped PostgreSQL advisory lock.

Issue #277. Several runtime facilities are process-local by design — the
in-process event bus (SSE fan-out), the login rate limiter, Studio chat
sessions, and the startup ``reset_all_to_paused`` sweep — so the control
plane is single-replica per database. Accidentally scaling it horizontally
(the stock uvicorn/compose instinct) degrades silently: each symptom looks
like a different bug (see docs/architecture/deployment.md 单副本约束).

This probe turns that silent degradation into a startup log line. On
lifespan entry it takes one *session-level* advisory lock
(``pg_try_advisory_lock``, a fixed key) on a dedicated pooled connection
that is held until shutdown:

- Session-level, not transaction-level: the lock must outlive every
  checkout/return cycle, and a transaction-scoped lock would be released
  the moment the owning transaction commits. db/schema.py's migration lock
  only needs to serialize one transaction, so it can use the xact variant;
  this one cannot.
- Held on a dedicated checked-out connection, not per query: a pooled
  connection released back to the pool drops its session locks, so the
  probe checks out one connection in the lifespan and keeps it until
  shutdown — exactly one pool slot for the process's lifetime, returned
  via ``close()`` in the lifespan finally. Tests interleave this with
  ``close_database_pools`` (per-test isolation), where a checked-out
  connection simply gets discarded: psycopg_pool's close() leaves
  out-standing connections alone and putconn-on-closed-pool closes them,
  so no leak and no crash either way.

Warning, not fail-fast: in a self-hosted single-machine deployment an
operator may legitimately run two Host instances (two worktrees) against
*different* databases on the same cluster — the advisory key is hashed
with ``current_database()`` so cross-database pairs never collide, and a
genuinely multi-replica deployment that knowingly accepts the degraded
mode can set ``AGENT_LEGION_ALLOW_MULTI_REPLICA=1`` to downgrade the log
line to info. Fail-fast would break the legitimate same-cluster cases for
a condition that is "unsupported", not "dangerous" — unlike the shared-db
schema guard (schema_guard.py), nothing here corrupts data; the harm is
bounded to degraded behavior that one log line surfaces.

``AGENT_LEGION_SKIP_SINGLE_REPLICA_PROBE=1`` disables the probe entirely
(tests, offline tooling): with the lock skipped the app behaves exactly as
it did before this module existed. Tests exercise the probe through the
env flag and mocks; the live path is exercised by the normal postgres
suite (every TestClient lifespan takes the lock once per app).
"""

from __future__ import annotations

import logging
import os

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.db.dialect import ConnectSource

logger = logging.getLogger(__name__)

_ALLOW_MULTI_REPLICA_ENV = "AGENT_LEGION_ALLOW_MULTI_REPLICA"
_SKIP_PROBE_ENV = "AGENT_LEGION_SKIP_SINGLE_REPLICA_PROBE"

# Fixed lock key; combined with current_database() below so two Host
# instances against two databases on one cluster do not fight over it
# (same scoping precedent as db/schema.py's migration lock).
_REPLICA_LOCK_KEY = "agent-legion-single-replica-probe"

_TRY_LOCK_SQL = "select pg_try_advisory_lock(hashtext(%s || current_database())) as acquired"
_UNLOCK_SQL = "select pg_advisory_unlock(hashtext(%s || current_database()))"


class SingleReplicaProbe:
    """Owns the dedicated connection holding the replica-detection lock."""

    def __init__(self, db_source: ConnectSource) -> None:
        self._db_source = db_source
        self._connection: DatabaseConnection | None = None
        self._lock_acquired: bool | None = None

    @property
    def lock_acquired(self) -> bool | None:
        """True/False after probe(); None when the probe was skipped."""
        return self._lock_acquired

    def probe(self) -> bool:
        """Take the advisory lock on a dedicated pooled connection.

        Returns True when this process is the sole detected replica (also
        true when skipped). Called from the lifespan; the checkout and the
        try-lock are two quick statements against an already-open pool —
        acceptable at startup, where the app performs many blocking DB
        calls during construction already.
        """
        if os.environ.get(_SKIP_PROBE_ENV) == "1":
            self._lock_acquired = None
            return True
        conn = connect_database(self._db_source)
        try:
            row = conn.execute(_TRY_LOCK_SQL, (_REPLICA_LOCK_KEY,)).fetchone()
        except Exception:
            # Probe failure must never block startup: release the checkout
            # and leave the runtime exactly as it was pre-#277.
            conn.close()
            logger.debug("single-replica probe failed", exc_info=True)
            self._lock_acquired = None
            return True
        acquired = bool(row and row["acquired"])
        self._lock_acquired = acquired
        if not acquired:
            self._log_conflict()
        # Keep the connection checked out for the process lifetime:
        # releasing it back to the pool would drop the session lock and
        # silently disable detection for every future starter.
        self._connection = conn
        return acquired

    def _log_conflict(self) -> None:
        if os.environ.get(_ALLOW_MULTI_REPLICA_ENV) == "1":
            logger.info(
                "single-replica probe: another Host replica holds the "
                "advisory lock for this database; multi-replica mode "
                "acknowledged via %s=1",
                _ALLOW_MULTI_REPLICA_ENV,
            )
            return
        logger.warning(
            "single-replica probe: another Host replica appears to be running "
            "against this database (advisory lock %s held elsewhere). The "
            "control plane is single-replica: SSE events, login rate limiting, "
            "Studio chat sessions, and pause state do not span replicas. Set "
            "%s=1 to acknowledge this deployment, or run exactly one replica "
            "per database.",
            _REPLICA_LOCK_KEY,
            _ALLOW_MULTI_REPLICA_ENV,
        )

    def close(self) -> None:
        """Release the lock and return the connection to the pool.

        Best-effort by design: a broken connection drops the advisory lock
        server-side anyway (session locks die with the session), and the
        tests' per-test ``close_database_pools`` may have already discarded
        the pool — ``putconn`` on a closed pool just closes the connection
        (psycopg_pool semantics), so the return path degrades gracefully
        instead of raising during shutdown.
        """
        conn = self._connection
        self._connection = None
        if conn is None:
            return
        try:
            if self._lock_acquired:
                conn.execute(_UNLOCK_SQL, (_REPLICA_LOCK_KEY,))
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            logger.debug("single-replica probe: unlock during shutdown failed", exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                logger.debug(
                    "single-replica probe: connection return during shutdown failed",
                    exc_info=True,
                )
