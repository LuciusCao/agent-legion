"""The pure reads of studio_publish_requests (#429 四轮 split).

Split from studio_publish_requests.py (file budget): the read half of the
handshake's persistence — the poll's pending read, the confirming read the
create guard and the poll use, and the by-id reads (with the status read's
lazy expiry riding its UPDATE). No write-side transitions here.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.studio_publish_request_claims import _REQUEST_COLUMNS


class StudioPublishRequestReadQueriesMixin(ConnectionQueriesMixin):
    """Reads for the handshake; composed into the JobQueries facade next to
    the create/claims mixins."""

    def get_pending_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's pending request, None when there is none.

        A pure read (pooled connection, no write transaction, no TTL filter):
        the Studio frontend polls this every 5s, and the read must not turn
        the poll into write load — the old read-then-sweep design opened a
        write connection on EVERY poll (#429). A pending row past its
        ``expires_at`` still surfaces here; the service layer decides
        whether the row is expired (and only then writes, via
        ``expire_pending_publish_request``), so this read itself issues zero
        writes (#429 二轮复审 NIT：认证 session 的滑动过期仍会写，不在本
        路径的断言范围内).
        """
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests"
                " where workspace_id=%s and status='pending'"
                " order by created_at desc limit 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_confirming_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's confirming row, None when there is none. The
        create path's re-request refusal and the poll's live-confirming
        surfacing (#429 三轮 P1-2 / 四轮 P3-2): a new pending row must not
        displace a row whose publish is in flight, and the dialog stays up
        while it is."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests"
                " where workspace_id=%s and status='confirming'"
                " order by created_at desc limit 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_publish_request(self, request_id: str) -> dict[str, Any] | None:
        """One request by id (any status); a pending row past expiry is
        lazily flipped to ``expired`` by the read itself (the UPDATE's
        expires_at predicate rides the statement, so no Python-side datetime
        parsing of the driver's serialized value)."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where id=%s and status='pending' and expires_at < current_timestamp"
                " returning " + _REQUEST_COLUMNS,
                (request_id,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    f"select {_REQUEST_COLUMNS} from studio_publish_requests where id=%s",
                    (request_id,),
                ).fetchone()
        return dict(row) if row is not None else None

    def get_publish_request_current_state(self, request_id: str) -> dict[str, Any] | None:
        """One request by id as it stands right now — pure read, no expiry
        write. The losing side of the confirm race uses this to report the
        request's real terminal state (#429): when a confirm's publish
        landed but the resolve lost a race (superseded mid-publish), the
        row still reads back with its final status for the response."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests where id=%s",
                (request_id,),
            ).fetchone()
        return dict(row) if row is not None else None
