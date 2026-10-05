"""Read-only DB adapter for the Agent backfill dry-run report (#934, #440).

The dry-run walks every workspace's active revision and Studio draft plus
the Agent catalog, and must never write. Two structural guards:

* the facade is built without ``JobQueriesBase.__init__`` (mirrors
  ``global_settings_kv_from_dsn``): the normal constructor runs ``init_db``
  (schema DDL under an advisory lock) — a write;
* every pooled connection is opened with
  ``default_transaction_read_only=on``, so any write statement reaching
  the server (from this report or a helper it reuses) fails with
  ``ReadOnlySqlTransaction`` instead of landing.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from server.app.jobs.queries import JobQueries

_READ_ONLY_OPTION = "-c default_transaction_read_only=on"


def read_only_dsn(dsn: str) -> str:
    """*dsn* (a PostgreSQL URL) with server-enforced read-only transactions.

    Appends to an existing ``options`` query parameter (e.g. a test schema
    ``search_path``) instead of replacing it.
    """
    parts = urlsplit(dsn)
    query = parse_qsl(parts.query, keep_blank_values=True)
    existing = " ".join(value for key, value in query if key == "options").strip()
    rest = [(key, value) for key, value in query if key != "options"]
    options = f"{existing} {_READ_ONLY_OPTION}".strip()
    # libpq URIs percent-decode but never read ``+`` as a space: quote, not quote_plus.
    query_text = urlencode([*rest, ("options", options)], quote_via=quote, safe="")
    return urlunsplit(parts._replace(query=query_text))


class AgentBackfillReader(JobQueries):
    """JobQueries plus the materialized Agent-route read the report needs."""

    def agent_route_targets(self, workspace_id: str) -> dict[str, str]:
        """node_key → Agent route target id (``workspace_node_routes``) of one workspace."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select node_key, target_id from workspace_node_routes"
                " where workspace_id=%s and target_kind='agent'",
                (workspace_id,),
            ).fetchall()
        return {str(row["node_key"]): str(row["target_id"]) for row in rows}


def agent_backfill_reader_from_dsn(dsn: str) -> AgentBackfillReader:
    """A facade bound to the read-only DSN; never runs ``init_db``.

    Only read methods are meant to be called; a write raises at the server.
    ``jobs_dir`` is deliberately unset — nothing in the report touches it.
    """
    reader = AgentBackfillReader.__new__(AgentBackfillReader)
    reader._path = read_only_dsn(dsn)  # data-layer-private field (see queries/base.py)
    return reader
