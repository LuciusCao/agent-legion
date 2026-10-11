"""Confirm-time heartbeat contract for agent-initiated publish requests (#464).

Split from tests/routes/test_studio_publish_request_races.py (test-file line
budget): the #464 owner-liveness lease — a confirming row renewed by the
confirm's heartbeat thread survives the 300s stale sweep, a dead owner's row
is still swept, the renewal predicate only touches confirming rows, and the
claim rolls back when the heartbeat thread cannot start (#1256 codex P2).
The seeding helpers live in studio_publish_request_testlib.py.
"""

from __future__ import annotations

import pytest

from tests.routes.studio_publish_request_testlib import (
    DRAFT_YAML as _DRAFT_YAML,
)
from tests.routes.studio_publish_request_testlib import (
    pending as _pending,
)
from tests.routes.studio_publish_request_testlib import (
    put_draft as _put_draft,
)
from tests.routes.studio_publish_request_testlib import (
    request_publish as _request_publish,
)
from tests.routes.studio_publish_request_testlib import (
    scoped_client as _scoped_client,
)
from tests.routes.studio_publish_request_testlib import (
    seed_workspace as _seed_workspace,
)

# -- #464: the confirm-time heartbeat keeps a slow publish alive -------------


def _wait_for_db(predicate, job_db, timeout: float = 5.0) -> None:
    """Poll a DB predicate until it holds (signal, not duration — the
    heartbeat loop runs on its own thread and wall-clock sleeps would just
    hope the renewal landed). Fails loudly on the deadline."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(job_db):
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for the heartbeat to renew claimed_at")


def test_heartbeated_confirming_row_survives_past_stale_threshold(
    client, job_db, monkeypatch
) -> None:
    """#464 回归 (a): an executing publish whose claim is being renewed must
    NOT be swept even when the publish outruns CONFIRMING_STALE_SECONDS. The
    heartbeat loop bumps claimed_at every interval, so the poll's stale
    predicate (claimed_at < now - 300s) never matches a live claim — the row
    resolves confirmed and the receipt is honest."""
    from server.app.services import studio_publish_request_support as support

    # Shrink the heartbeat interval so the renewal actually fires inside a
    # test: the interval constant is the loop's only clock; the production
    # value (60s) is derivation-anchored in the module (TTL/5) and untouched.
    monkeypatch.setattr(support, "_CLAIM_HEARTBEAT_INTERVAL_SECONDS", 0.05)

    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft
    service_job_db = client.app.state.job_db

    def claimed_at_value(db):
        with db.connect() as conn:
            row = conn.execute(
                "select claimed_at from studio_publish_requests where id=%s",
                (request_id,),
            ).fetchone()
        return row["claimed_at"]

    def slow_publish_then_age_past_threshold(job_db_, workspace_id_, yaml, enabled):
        # 1) The claim landed (this hook runs after it). Wait for the first
        #    renewal: claimed_at MOVING past its claim-time value is the
        #    observable signal that the heartbeat thread is live.
        claim_time = claimed_at_value(service_job_db)
        assert claim_time is not None
        _wait_for_db(lambda db: claimed_at_value(db) != claim_time, service_job_db)
        # 2) Age the claim past the sweep threshold while the publish is
        #    still executing — without the next renewal the poll below would
        #    flip the row to expired.
        with service_job_db.connect() as conn:
            conn.execute(
                "update studio_publish_requests set claimed_at=current_timestamp"
                " - interval '400 seconds' where id=%s",
                (request_id,),
            )

        # 3) The next heartbeat beat renews the aged row (signal: the stale
        #    predicate no longer matches), so the poll sweep finds nothing.
        def renewed(db):
            with db.connect() as conn:
                return (
                    conn.execute(
                        "select 1 from studio_publish_requests where id=%s"
                        " and claimed_at > current_timestamp - interval '300 seconds'",
                        (request_id,),
                    ).fetchone()
                    is not None
                )

        _wait_for_db(renewed, service_job_db)
        polled = _pending(client, workspace_id_).json()["request"]
        assert polled is not None, "renewed claim must survive the poll sweep"
        assert polled["status"] == "confirming"
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(
        service_module, "publish_workflow_draft", slow_publish_then_age_past_threshold
    )
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "confirmed"


def test_stale_confirming_row_without_heartbeat_is_still_swept(client, job_db, monkeypatch) -> None:
    """#464 回归 (b): the heartbeat only protects LIVE owners. A claim whose
    process died between claim and resolve (no heartbeats can land anymore)
    must still be swept by the poll once past the threshold — the renewal is
    an owner-liveness signal, not an exemption from the predicate.

    Deterministic by construction: the production 60s interval is NOT
    shrunk here, so no heartbeat can land within the test's lifetime — the
    dead owner is exactly the no-renewal case."""
    from server.app.services.studio_publish_request_support import (
        CONFIRMING_STALE_SECONDS,
    )

    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]
    service_job_db = client.app.state.job_db

    from server.app.services import studio_publish_requests as service_module

    def die_between_claim_and_resolve(job_db_, workspace_id_, yaml, enabled):
        # The process dies mid-publish (this is the last statement it runs):
        # age the claim past the threshold, then "crash". The confirm's
        # broad-except rollback would normally move the row back to pending,
        # so the death is completed by also swallowing the rollback (the
        # resolve never runs — same shape as the #429 sweep test above).
        with service_job_db.connect() as conn:
            conn.execute(
                "update studio_publish_requests set claimed_at=current_timestamp"
                f" - interval '{int(CONFIRMING_STALE_SECONDS) + 60} seconds'"
                " where id=%s",
                (request_id,),
            )
        raise RuntimeError("simulated process death between claim and resolve")

    def resolve_never_runs(*args, **kwargs):
        # The dead process's rollback/resolve: no-op (None = lost race).
        return None

    monkeypatch.setattr(service_module, "publish_workflow_draft", die_between_claim_and_resolve)
    monkeypatch.setattr(service_job_db, "resolve_publish_request", resolve_never_runs)
    with pytest.raises(RuntimeError, match="simulated process death"):
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
        )
    monkeypatch.undo()

    # No rollback ran (the crash ate it) and no heartbeat can ever land for
    # the dead owner: the row sits confirming past the threshold, and the
    # poll's sweep must flip it to expired.
    assert _pending(client, workspace_id).json()["request"] is None
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "expired"


def test_heartbeat_renews_only_a_confirming_row(client, job_db) -> None:
    """#464 谓词守卫钉: the renewal's ``status='confirming'`` predicate rides
    the UPDATE — a pending (unclaimed) row and a nonexistent id both report
    rowcount=0 (False) and stay untouched, so the heartbeat can never
    resurrect or fabricate a claim on a row another transition owns."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    # Pending row: not claimed, so the renewal must refuse.
    assert job_db.heartbeat_confirming_publish_request(request_id) is False
    with job_db.connect() as conn:
        row = conn.execute(
            "select status, claimed_at from studio_publish_requests where id=%s",
            (request_id,),
        ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_at"] is None
    # Unknown id: same False, no error.
    assert job_db.heartbeat_confirming_publish_request("no-such-request") is False

    # A genuinely confirming row renews (the positive half of the predicate).
    job_db.claim_pending_publish_request(workspace_id, request_id, request["draft_hash"])
    assert job_db.heartbeat_confirming_publish_request(request_id) is True


# -- #1256 codex P2: the claim rolls back when the heartbeat cannot start ----


def test_heartbeat_thread_start_failure_rolls_back_to_pending(client, job_db, monkeypatch) -> None:
    """#1256 codex P2: ``thread.start()`` can raise (process at the thread
    limit). The claim (pending → confirming) is already COMMITTED at that
    point and the exception surfaces before the context manager yields, so
    without a rollback the row would sit confirming — uncancellable,
    409ing every new request — until the 300s sweep. The confirm must roll
    the row back to pending (the same recovery the publish-pipeline failure
    gets) and the caller sees the 500."""
    import threading

    from server.app.services import studio_publish_request_support as support

    real_thread = threading.Thread

    class ExplodingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("simulated thread limit reached")

        def join(self, timeout=None):
            raise AssertionError("join on a thread that never started")

    import types

    monkeypatch.setattr(
        support, "threading", types.SimpleNamespace(Thread=ExplodingThread, Event=threading.Event)
    )
    assert real_thread is threading.Thread  # the patch is scoped to the support module

    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    # The TestClient re-raises unhandled server exceptions (production would
    # answer 500 through the error middleware); the row state is the contract.
    with pytest.raises(RuntimeError, match="simulated thread limit"):
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
        )

    # Back to pending — not stuck confirming: the poll surfaces the request,
    # cancel works again, no 300s wedge.
    polled = _pending(client, workspace_id).json()["request"]
    assert polled is not None
    assert polled["id"] == request_id
    assert polled["status"] == "pending"
    cancelled = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/cancel"
    )
    assert cancelled.status_code == 200, cancelled.text
    # No publish happened: the active revision is still the seeded one.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 1


def test_confirm_resolve_failure_keeps_confirming_not_pending(client, job_db, monkeypatch) -> None:
    """Family boundary pin (#1256 census): the rollback covers claim →
    heartbeat start → publish, but NOT the resolve step. Once the publish
    landed, flipping the row back to pending would invite a second publish
    of the same draft — a resolve-time failure must leave the row
    ``confirming`` for the stale sweep (the _resolve_lost_race / sweep
    contract), never roll it back."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]
    service_job_db = client.app.state.job_db
    real_resolve = service_job_db.resolve_publish_request

    def resolve_raises_on_confirmed(*args, **kwargs):
        # Only the confirming→confirmed transition fails (simulated DB drop);
        # a pending-rollback write stays possible so a mis-scoped rollback
        # would REALLY flip the row and be observable below.
        if kwargs.get("status") == "confirmed":
            raise RuntimeError("simulated DB drop at resolve")
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(service_job_db, "resolve_publish_request", resolve_raises_on_confirmed)

    with pytest.raises(RuntimeError, match="simulated DB drop at resolve"):
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
        )
    monkeypatch.undo()

    # The publish effect is real and the row stays confirming (the sweep's
    # ownership), NOT pending — a pending row here would be re-confirmable
    # and double-publish the same draft.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2
    with service_job_db.connect() as conn:
        row = conn.execute(
            "select status from studio_publish_requests where id=%s", (request_id,)
        ).fetchone()
    assert row["status"] == "confirming"
