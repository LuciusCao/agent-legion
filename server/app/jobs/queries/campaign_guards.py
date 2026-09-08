"""Quota-guarded campaign writes (#532 PR-A, PR #541 P2).

The per-workspace active-campaign cap (max_active_per_workspace) is a
count-then-write invariant, and count-then-write races: two concurrent
creates each read the workspace below the cap and each land a row; a pause
frees a slot that new creates fill, and the resume that follows would push
the workspace past the cap. Both guarded paths here fix the race the same
way the studio publish-request handshake does (#429 四轮 codex P1): the
count and the write share ONE transaction held under a per-workspace
``pg_advisory_xact_lock``, so the second arrival re-counts after the first
commits and refuses. The lock namespace is unique to the campaigns table;
the plain (unguarded) row lifecycle stays in ``queries/campaigns.py``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.campaigns import (
    _CAMPAIGN_COLUMNS,
    CampaignQueriesMixin,
    campaign_record,
)
from server.app.services.job_errors import ConflictError

# Advisory-lock key namespace for the quota's per-workspace critical
# section: create (count + insert) and resume (count + paused→running)
# serialize on it. Any 63-bit constant unique to this table works;
# hashtext(workspace_id) spreads the keys. Kept distinct from the
# publish-request namespace (416429) and the schema-migration lock.
_CAMPAIGN_LOCK_NAMESPACE = 416532

_QUOTA_MESSAGE = (
    "Workspace already has {max_active} active campaigns (pending/running);"
    " cancel or complete one {action}"
)


def lock_workspace(conn: Any, workspace_id: str) -> None:
    """Take the quota's per-workspace advisory lock (transaction-scoped:
    released at COMMIT/ROLLBACK)."""
    conn.execute(
        "select pg_advisory_xact_lock(%s, hashtext(%s))",
        (_CAMPAIGN_LOCK_NAMESPACE, workspace_id),
    )


def count_active_campaigns_tx(conn: Any, workspace_id: str) -> int:
    """In-transaction active count for the guarded create/resume paths."""
    row = conn.execute(
        "select count(*) as n from campaigns"
        " where workspace_id=%s and status in ('pending', 'running')",
        (workspace_id,),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _quota_error(max_active: int, *, resuming: bool) -> ConflictError:
    return ConflictError(
        _QUOTA_MESSAGE.format(
            max_active=max_active, action="before resuming" if resuming else "first"
        )
    )


class CampaignGuardQueriesMixin(CampaignQueriesMixin):
    """The campaign row lifecycle PLUS the count-then-write quota guards.

    Subclasses CampaignQueriesMixin so the composed facade lists one entry
    and every guarded call site keeps the plain row reads (get/list/count)
    on the same object; the guards themselves live in this module for file
    budget, sharing the column projection and record mapper with
    ``queries/campaigns.py``.
    """

    def create_campaign_guarded(
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
        max_active: int,
    ) -> dict[str, Any]:
        """Quota-checked insert in ONE transaction (PR #541 P2).

        The workspace advisory lock serializes the count and the insert, so
        two concurrent creates cannot both observe the workspace below the
        cap and each land a row (the unlocked count-then-insert reads stale
        snapshots under READ COMMITTED). Raises ConflictError at or above
        the cap (create refuses when the cap is already met).
        """
        campaign_id = campaign_id or uuid4().hex
        with self.connect() as conn:
            lock_workspace(conn, workspace_id)
            if count_active_campaigns_tx(conn, workspace_id) >= max_active:
                raise _quota_error(max_active, resuming=False)
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

    def resume_campaign_guarded(
        self,
        workspace_id: str,
        campaign_id: str,
        *,
        max_active: int,
    ) -> dict[str, Any] | None:
        """Quota-checked paused→running CAS in ONE transaction (PR #541 P2).

        A paused row is outside the active set, so filling the freed slots
        and resuming would re-exceed the cap; resume re-checks the count
        under the same lock create takes. Returns None only for the
        workspace-scope miss (the anti-enumeration None of
        get_campaign_in_workspace); returns the unchanged row for a
        non-paused status (the service maps that to its 409s); raises
        ConflictError for a full workspace.
        """
        with self.connect() as conn:
            lock_workspace(conn, workspace_id)
            row = conn.execute(
                f"select {_CAMPAIGN_COLUMNS} from campaigns where id=%s and workspace_id=%s",
                (campaign_id, workspace_id),
            ).fetchone()
            if row is None:
                return None
            current = campaign_record(dict(row))
            if current["status"] != "paused":
                return current
            if count_active_campaigns_tx(conn, workspace_id) >= max_active:
                raise _quota_error(max_active, resuming=True)
            updated = conn.execute(
                "update campaigns set status='running', updated_at=current_timestamp"
                f" where id=%s and status='paused' returning {_CAMPAIGN_COLUMNS}",
                (campaign_id,),
            ).fetchone()
        if updated is None:
            raise ConflictError("Campaign state changed concurrently; retry")
        return campaign_record(dict(updated))
