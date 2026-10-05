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

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
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


def agent_backfill_reader_from_dsn(dsn: str) -> JobQueries:
    """A JobQueries facade bound to the read-only DSN; never runs ``init_db``.

    Only read methods are meant to be called; a write raises at the server.
    ``jobs_dir`` is deliberately unset — nothing in the report touches it.
    """
    from server.app.jobs.queries import JobQueries

    reader = JobQueries.__new__(JobQueries)
    reader._path = read_only_dsn(dsn)  # data-layer-private field (see queries/base.py)
    return reader
