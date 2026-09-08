"""Campaign row persistence (schema v80, #532 / #505, BOUNDARY-DATA-001).

The campaigns table is the drip-feed product's state carrier: one row = one
watermark-gated bulk operation the feeder drains in batches. This mixin owns
the row lifecycle the service drives — create, list/get reads, the CAS state
transitions (pause / resume / cancel / feeder advance), and the feeder's
active scan. The cursor-advance discipline (design §1.4): the update carries
the OLD progress_json in its WHERE clause, so a pause/cancel that lands
between the feeder's read and its write wins the race — the feeder's batch
submissions stay (dedup/eligibility make them idempotent), only the cursor
does not advance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from server.app.db.rowmap import iso_optional
from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Terminal states: pause/resume refuse them (a finished campaign is not a
# runnable one); cancel refuses them (cancelling a cancelled campaign is a
# no-op error, not an idempotent success — the operator should see the
# current state instead of a misleading 200).
CAMPAIGN_TERMINAL_STATUSES = frozenset({"failed", "completed", "cancelled"})
# States the feeder's active scan matches (idx_campaigns_active partial
# index): pending = created but not yet picked up, running = being drained.
CAMPAIGN_ACTIVE_STATUSES = ("pending", "running")

# Public column projection for the list/read paths (created_at ordering rides
# the idx_campaigns_workspace index).
_CAMPAIGN_COLUMNS = (
    "id, workspace_id, mode, status, target_spec_json, progress_json,"
    " watermark, batch_size, batches_submitted, jobs_succeeded, jobs_skipped,"
    " jobs_failed, error_message, created_by, created_at, updated_at,"
    " finished_at"
)


def campaign_record(row: dict[str, Any]) -> dict[str, Any]:
    """Public campaign record: JSON columns decoded, timestamps ISO-encoded."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "mode": str(row["mode"]),
        "status": str(row["status"]),
        "target_spec": _parse_object(row.get("target_spec_json")),
        "progress": _parse_object(row.get("progress_json")),
        "watermark": int(row["watermark"]),
        "batch_size": int(row["batch_size"]),
        "batches_submitted": int(row["batches_submitted"]),
        "jobs_succeeded": int(row["jobs_succeeded"]),
        "jobs_skipped": int(row["jobs_skipped"]),
        "jobs_failed": int(row["jobs_failed"]),
        "error_message": str(row["error_message"] or ""),
        "created_by": str(row["created_by"] or ""),
        "created_at": iso_optional(row["created_at"]),
        "updated_at": iso_optional(row["updated_at"]),
        "finished_at": iso_optional(row["finished_at"]),
    }


def _parse_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class CampaignQueriesMixin(ConnectionQueriesMixin):
    def generate_campaign_id(self) -> str:
        """Allocate a campaign id before the row exists.

        The submit path needs the id to build the object-store manifest key
        BEFORE the insert (upload → validate → insert in one fail-fast
        request; a two-phase row would add a pending-upload state this slice
        deliberately avoids). ``create_campaign`` accepts the pre-allocated
        id via ``campaign_id=``.
        """
        return uuid4().hex

    def create_campaign(
        self,
        workspace_id: str,
        mode: str,
        target_spec: dict[str, Any],
        *,
        watermark: int,
        batch_size: int,
        created_by: str = "",
        campaign_id: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a pending campaign row; the id defaults to a fresh uuid4 hex.

        ``campaign_id`` carries a pre-allocated id (see generate_campaign_id)
        whose manifest object may already exist in the bucket; on any insert
        failure that object is an unreferenced orphan the bucket TTL/GC
        eventually reaps (a compensating delete would race retries).
        ``progress`` is the initial cursor document (default ``{}``; the
        service seeds ``{"item_offset": 0}``-shaped cursors) — the feeder's
        CAS advance compares against the stored document, so the initial
        shape must be deliberate, never an accident of the default.
        """
        campaign_id = campaign_id or uuid4().hex
        with self.connect() as conn:
            row = conn.execute(
                "insert into campaigns(id, workspace_id, mode, target_spec_json,"
                " progress_json, watermark, batch_size, created_by)"
                f" values (%s, %s, %s, %s, %s, %s, %s, %s) returning {_CAMPAIGN_COLUMNS}",
                (
                    campaign_id,
                    workspace_id,
                    mode,
                    json.dumps(target_spec, ensure_ascii=False, sort_keys=True),
                    json.dumps(progress or {}, ensure_ascii=False, sort_keys=True),
                    watermark,
                    batch_size,
                    created_by,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("campaign insert did not return a row")
        return campaign_record(dict(row))

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        if not campaign_id:
            return None
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_CAMPAIGN_COLUMNS} from campaigns where id=%s", (campaign_id,)
            ).fetchone()
        return campaign_record(dict(row)) if row else None

    def get_campaign_in_workspace(
        self, workspace_id: str, campaign_id: str
    ) -> dict[str, Any] | None:
        """Workspace-scoped read; None covers both absent and cross-workspace."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_CAMPAIGN_COLUMNS} from campaigns where id=%s and workspace_id=%s",
                (campaign_id, workspace_id),
            ).fetchone()
        return campaign_record(dict(row)) if row else None

    def list_campaigns(self, workspace_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_CAMPAIGN_COLUMNS} from campaigns where workspace_id=%s"
                " order by created_at desc, id desc limit %s",
                (workspace_id, limit),
            ).fetchall()
        return [campaign_record(dict(row)) for row in rows]

    def count_active_campaigns(self, workspace_id: str) -> int:
        """Pending/running rows in this workspace (the create-path cap check)."""
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as n from campaigns"
                " where workspace_id=%s and status in ('pending', 'running')",
                (workspace_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_active_campaigns(self) -> list[dict[str, Any]]:
        """Feeder's per-tick scan; hits the idx_campaigns_active partial index."""
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select {_CAMPAIGN_COLUMNS} from campaigns"
                " where status in ('pending', 'running')"
                " order by workspace_id, id"
            ).fetchall()
        return [campaign_record(dict(row)) for row in rows]

    def transition_campaign_status(
        self,
        campaign_id: str,
        from_statuses: tuple[str, ...],
        to_status: str,
        *,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        """CAS status transition; None means the row was not in from_statuses.

        Terminal transitions (failed/completed/cancelled) stamp finished_at.
        Used by pause (running→paused), resume (paused→running), cancel
        (non-terminal→cancelled), and the feeder's pending→running pickup
        and terminal flips.
        """
        if not from_statuses:
            return None
        placeholders = ",".join("%s" for _ in from_statuses)
        finished = to_status in CAMPAIGN_TERMINAL_STATUSES
        # Comma-separated clause tails (finished_at / error_message), each
        # empty or ", col=%s" — one UPDATE statement, no conditional SQL
        # fragments left dangling.
        tail = ""
        params: list[Any] = [to_status]
        if finished:
            tail += ", finished_at=current_timestamp"
        if error_message is not None:
            tail += ", error_message=%s"
            params.append(error_message)
        # WHERE order: id=%s first, then the status placeholders.
        params.extend([campaign_id, *from_statuses])
        with self.connect() as conn:
            row = conn.execute(
                "update campaigns set status=%s, updated_at=current_timestamp"
                f"{tail} where id=%s and status in ({placeholders})"
                f" returning {_CAMPAIGN_COLUMNS}",
                params,
            ).fetchone()
        return campaign_record(dict(row)) if row else None

    def advance_campaign_progress(
        self,
        campaign_id: str,
        *,
        expected_progress: dict[str, Any],
        progress: dict[str, Any],
        batches_submitted: int,
        jobs_succeeded: int,
        jobs_skipped: int,
        jobs_failed: int,
    ) -> dict[str, Any] | None:
        """CAS cursor advance: only when the row still holds expected_progress.

        The feeder rewrites the whole progress document (cursor + watermark
        samples), so the equality guard on the OLD document makes a racing
        pause/cancel the winner: this update misses, the feed loop stops on
        the next active-scan miss. Counters are absolute (the caller passes
        the new totals), so a lost race never double-counts — the surviving
        row keeps the last committed values.
        """
        with self.connect() as conn:
            row = conn.execute(
                "update campaigns set progress_json=%s, batches_submitted=%s,"
                " jobs_succeeded=%s, jobs_skipped=%s, jobs_failed=%s,"
                " updated_at=current_timestamp"
                " where id=%s and status in ('pending', 'running')"
                " and progress_json=%s"
                f" returning {_CAMPAIGN_COLUMNS}",
                (
                    json.dumps(progress, ensure_ascii=False, sort_keys=True),
                    batches_submitted,
                    jobs_succeeded,
                    jobs_skipped,
                    jobs_failed,
                    campaign_id,
                    json.dumps(expected_progress, ensure_ascii=False, sort_keys=True),
                ),
            ).fetchone()
        return campaign_record(dict(row)) if row else None

    def campaign_updated_now(self, campaign_id: str) -> None:
        """Touch updated_at without changing state (feeder's pickup flip)."""
        with self.connect() as conn:
            conn.execute(
                "update campaigns set updated_at=%s where id=%s",
                (datetime.now(UTC), campaign_id),
            )
