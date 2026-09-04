"""Schema v76 (#416): the studio_publish_requests lifecycle at the DB layer.

Route-level behavior (auth matrix, publish gates) lives in
tests/routes/test_studio_publish_requests.py; this file pins the migration
record, the table shape (including the #429 三轮 P1 hardening: the
draft_hash column and the partial unique index that makes one-pending-per-
workspace a database invariant), and the queries-layer state machine
(supersede / lazy expiry / atomic claim + resolve) against real Postgres.
"""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_schema_v76_recorded() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (76,)).fetchone()
    assert row is not None
    assert row["name"] == "studio_publish_requests"


def test_publish_requests_columns() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='studio_publish_requests'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "workspace_id",
        "chat_session_id",
        "status",
        "created_by",
        "result_revision_id",
        "draft_hash",
        "created_at",
        "expires_at",
        "resolved_at",
        "claimed_at",
    }


def test_status_check_rejects_unknown_states() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into studio_publish_requests(workspace_id, status, expires_at)"
            " values ('demo_workflow', 'bogus', now() + interval '1 minute')"
        )


def test_one_pending_row_per_workspace_enforced_by_index() -> None:
    """#429 三轮 P1-1: the partial unique index is the boundary — a second
    pending row for one workspace is rejected by the database itself, no
    matter which code path (or direct SQL) attempts it. The index exists as
    a partial unique index on both the fresh and the upgraded paths (the
    parity test pins the two shapes together)."""
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into studio_publish_requests(workspace_id, expires_at)"
            " values ('demo_workflow', now() + interval '1 minute'),"
            " ('demo_workflow', now() + interval '1 minute')"
        )
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select indexdef from pg_indexes"
            " where schemaname=current_schema()"
            " and indexname='idx_studio_publish_requests_pending_workspace'"
        ).fetchone()
    assert row is not None
    assert "unique" in str(row["indexdef"]).lower()
    assert "status = 'pending'" in str(row["indexdef"])


def test_claim_moves_pending_to_confirming_and_back() -> None:
    """#429 三轮 P1-2/P1-3 at the queries layer: the claim is one atomic
    UPDATE (pending → confirming) that also re-checks TTL and the draft
    hash; a refused publish resets the row to pending through the same
    resolve entry point."""
    from server.app.jobs.queries.studio_publish_requests import (
        StudioPublishRequestQueriesMixin,
    )

    job_db = StudioPublishRequestQueriesMixin.__new__(StudioPublishRequestQueriesMixin)
    job_db._path = TEST_DATABASE_URL  # noqa: SLF001 — test wiring
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('claim_ws', 'Claim WS', 'claim_ws') on conflict (id) do nothing"
        )
    request = job_db.create_pending_publish_request(
        "claim_ws", "studio-agent:test", draft_hash="hash-a"
    )
    request_id = str(request["id"])

    # Hash mismatch: the claim refuses (None) and the row stays pending.
    assert job_db.claim_pending_publish_request("claim_ws", request_id, "hash-b") is None
    assert job_db.get_pending_publish_request("claim_ws")["status"] == "pending"

    # Matching hash: the row moves to confirming — and while confirming, it
    # is invisible to the pending read (cancel/new-request predicates).
    # The claim stamps claimed_at (#429 四轮 P1: the stale-claim clock).
    claimed = job_db.claim_pending_publish_request("claim_ws", request_id, "hash-a")
    assert claimed is not None and claimed["status"] == "confirming"
    assert claimed["claimed_at"] is not None
    assert job_db.get_pending_publish_request("claim_ws") is None

    # A refused publish rolls the row back to pending (retryable); the
    # rollback clears claimed_at (the next claim stamps it fresh).
    rolled_back = job_db.resolve_publish_request(request_id, status="pending")
    assert rolled_back is not None and rolled_back["status"] == "pending"
    assert rolled_back["claimed_at"] is None
    assert job_db.get_pending_publish_request("claim_ws")["id"] == request_id

    # Success path: claim again, then confirm records the revision.
    assert job_db.claim_pending_publish_request("claim_ws", request_id, "hash-a") is not None
    resolved = job_db.resolve_publish_request(
        request_id, status="confirmed", result_revision_id="rev-1"
    )
    assert resolved is not None and resolved["status"] == "confirmed"
    assert resolved["result_revision_id"] == "rev-1"


def test_stale_confirming_row_is_expired_by_the_sweep() -> None:
    """#429 四轮 P1: a confirming row whose claim is older than
    CONFIRMING_STALE_SECONDS is a dead process's (killed between claim and
    resolve). The sweep flips it to ``expired`` — the agent's status tool
    sees an honest terminal state and the workspace's request slot is
    freed. A FRESH claim (within the threshold) never matches."""
    from server.app.jobs.queries.studio_publish_requests import (
        StudioPublishRequestQueriesMixin,
    )
    from server.app.services.studio_publish_request_support import (
        CONFIRMING_STALE_SECONDS,
    )

    job_db = StudioPublishRequestQueriesMixin.__new__(StudioPublishRequestQueriesMixin)
    job_db._path = TEST_DATABASE_URL  # noqa: SLF001 — test wiring
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('stale_ws', 'Stale WS', 'stale_ws') on conflict (id) do nothing"
        )
    request = job_db.create_pending_publish_request("stale_ws", "studio-agent:test")
    request_id = str(request["id"])

    # Fresh claim: within the threshold — the sweep must not touch it.
    assert job_db.claim_pending_publish_request("stale_ws", request_id, None) is not None
    assert job_db.expire_stale_confirming_publish_request("stale_ws") is None
    assert job_db.get_publish_request_current_state(request_id)["status"] == "confirming"

    # Simulate the dead process: the claim happened long ago (direct SQL —
    # the process that would have resolved the row is gone).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update studio_publish_requests set claimed_at = current_timestamp"
            f" - interval '{int(CONFIRMING_STALE_SECONDS) + 60} seconds'"
            " where id=%s",
            (request_id,),
        )

    swept = job_db.expire_stale_confirming_publish_request("stale_ws")
    assert swept is not None
    assert swept["id"] == request_id
    assert swept["status"] == "expired"
    assert swept["resolved_at"] is not None
    # The slot is free: a fresh pending row can be parked for the workspace.
    fresh = job_db.create_pending_publish_request("stale_ws", "studio-agent:test")
    assert fresh["status"] == "pending"
