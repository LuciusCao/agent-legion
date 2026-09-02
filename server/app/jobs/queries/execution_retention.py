"""Execution-plane terminal-row retention queries (issue #354).

The SQL half of ``services/execution_retention.py``: keyset page reads and
bounded batch removes for the three execution-plane tables. SQL lives in the
queries layer per BOUNDARY-DATA-001 (#281 pattern); the service module keeps
the retention policy, cursor persistence, and batch orchestration.

Keyset shape: each table pages on its cutoff column plus a tiebreaker id,
one page per short transaction, so no lock spans batches and an interrupt
resumes from the persisted cursor (see the service module).

Table order and predicates (the service walks the tables in this order;
all FKs are ``on delete cascade`` but explicit order keeps each page's
rowcount meaningful and never relies on the trigger path for the hot
tables):

1. ``agent_execution_requests`` — terminal ('done'/'cancelled') rows past
   the cutoff. No table references it; removing them first keeps the sweep
   from holding row locks a concurrent claim might touch.
2. ``executor_leases`` — non-active ('released'/'expired') rows past the
   cutoff. ``agent_execution_requests`` has no FK to it (only a plain
   ``lease_id`` text column), and terminal requests were already removed in
   step 1, so no dangling references remain. Active leases are never
   touched — the claim/heartbeat path is untouched by construction.
3. ``node_run_token_usage`` — rows past the cutoff whose node run is
   terminal. Usage rows reference ``node_runs``, which retention never
   removes (audit trail; their fat artifacts are reaped by cleanup_sweep,
   which also never removes rows).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Keyset page for one terminal state of agent_execution_requests. Each state
# gets its own cursor so both partial indexes (done/cancelled finished_at)
# serve their branch without a sort.
_REQUESTS_PAGE_SQL = """
select execution_id, finished_at
from agent_execution_requests
where state = %s
  and finished_at is not null
  and finished_at < %s
  and (finished_at > %s or (finished_at = %s and execution_id > %s))
order by finished_at, execution_id
limit %s
"""

# Non-active leases past the cutoff, keyset on (expires_at, id): the claim
# path's active-lease lookups filter on status='active', so this page never
# overlaps them.
_LEASES_PAGE_SQL = """
select id, expires_at
from executor_leases
where status != 'active'
  and expires_at < %s
  and (expires_at > %s or (expires_at = %s and id > %s))
order by expires_at, id
limit %s
"""

# Token usage past the cutoff whose node run is terminal (finished_at set):
# node_runs rows are never deleted by retention (audit trail; their fat
# artifacts are reaped by cleanup_sweep, which never deletes rows either),
# so usage rows of still-running executions are out of scope by construction.
_USAGE_PAGE_SQL = """
select u.id, u.created_at
from node_run_token_usage u
join node_runs n on n.id = u.node_run_id
where u.created_at < %s
  and n.finished_at is not null
  and (u.created_at > %s or (u.created_at = %s and u.id > %s))
order by u.created_at, u.id
limit %s
"""


class ExecutionRetentionQueriesMixin(ConnectionQueriesMixin):
    """Keyset page reads and bounded deletes for the retention sweep."""

    def page_terminal_agent_requests(
        self,
        state: str,
        cutoff: Any,
        last_at: Any,
        last_id: Any,
        limit: int,
    ) -> list[dict[str, Any]]:
        """One keyset page of terminal requests past the cutoff."""
        with self._connect_read() as conn:
            rows = conn.execute(
                _REQUESTS_PAGE_SQL, (state, cutoff, last_at, last_at, last_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def page_inactive_leases(
        self, cutoff: Any, last_at: Any, last_id: Any, limit: int
    ) -> list[dict[str, Any]]:
        """One keyset page of non-active leases past the cutoff."""
        with self._connect_read() as conn:
            rows = conn.execute(
                _LEASES_PAGE_SQL, (cutoff, last_at, last_at, last_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def page_finished_token_usage(
        self, cutoff: Any, last_at: Any, last_id: Any, limit: int
    ) -> list[dict[str, Any]]:
        """One keyset page of token-usage rows of finished runs past cutoff."""
        with self._connect_read() as conn:
            rows = conn.execute(
                _USAGE_PAGE_SQL, (cutoff, last_at, last_at, last_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_agent_requests(self, execution_ids: list[str]) -> int:
        """Delete one batch of requests in its own short transaction."""
        with self.write() as conn:
            return int(
                conn.execute(
                    "delete from agent_execution_requests where execution_id = any(%s)",
                    (execution_ids,),
                ).rowcount
            )

    def delete_leases(self, lease_ids: list[str]) -> int:
        """Delete one batch of leases in its own short transaction."""
        with self.write() as conn:
            return int(
                conn.execute(
                    "delete from executor_leases where id = any(%s)", (lease_ids,)
                ).rowcount
            )

    def delete_token_usage(self, usage_ids: list[int]) -> int:
        """Delete one batch of token-usage rows in its own short transaction."""
        with self.write() as conn:
            return int(
                conn.execute(
                    "delete from node_run_token_usage where id = any(%s)", (usage_ids,)
                ).rowcount
            )


def execution_retention_queries_from_dsn(dsn: str) -> ExecutionRetentionQueriesMixin:
    """Bare-DSN adapter for the retention mixin (#187 ConnectSource, #281).

    Test and CLI holders of a plain DSN string get the mixin methods without
    constructing JobQueries (whose ``__init__`` bootstraps the schema under
    an advisory lock and needs a jobs_dir); the private DSN field mirrors
    ``queries/base.py`` and ``global_settings_kv_from_dsn``.
    """
    queries = ExecutionRetentionQueriesMixin.__new__(ExecutionRetentionQueriesMixin)
    queries._path = dsn  # data-layer-private field (see queries/base.py)
    return queries
