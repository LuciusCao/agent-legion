"""Race and regression pins for agent-initiated publish requests (#429).

Split from tests/routes/test_studio_publish_requests.py (test-file line
budget): the concurrency contract — the pending-slot unique index races,
the confirming claim vs cancel/create/supersede, the stale-claim recovery
sweep, and the poll's live-confirming surfacing. The base behavioral
contract (park/confirm/cancel, gates, security matrix) stays in the
original file; the seeding helpers live in
studio_publish_request_testlib.py.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg.errors import UniqueViolation

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

# -- #429 三轮复审 P1 回归钉 ------------------------------------------------


def test_concurrent_creates_leave_exactly_one_pending(client, job_db) -> None:
    """P1-1: two concurrent tool calls on a workspace with no pending row.
    The partial unique index (workspace_id where status='pending') makes the
    loser's INSERT fail inside its transaction; the retry supersedes the
    winner's row and inserts its own. Either way: exactly ONE pending row
    afterwards — never two (two pending rows would let the poll surface one,
    resolve it, and re-surface the other for a second publish)."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: _request_publish(scoped, workspace_id),
                range(2),
            )
        )

    requests = []
    for response in responses:
        assert response.status_code == 200, response.text
        requests.append(response.json()["request"])
    assert requests[0]["id"] != requests[1]["id"]
    # Exactly one pending row: the loser's retry superseded the winner's row
    # and took over the slot (sequential-supersede semantics preserved).
    with client.app.state.job_db.connect() as conn:
        rows = conn.execute(
            "select id, status from studio_publish_requests where workspace_id=%s"
            " order by created_at desc",
            (workspace_id,),
        ).fetchall()
    assert [row["status"] for row in rows] == ["pending", "superseded"]
    assert rows[0]["id"] in {request["id"] for request in requests}
    assert _pending(client, workspace_id).json()["request"]["id"] == rows[0]["id"]
    # The superseded request reads back superseded through the status tool.
    superseded_id = rows[1]["id"]
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{superseded_id}")
    assert status.json()["request"]["status"] == "superseded"


def test_db_rejects_a_second_pending_row_directly(client, job_db) -> None:
    """P1-1 (the invariant itself): even a direct SQL INSERT cannot create a
    second pending row for a workspace — the partial unique index is the
    boundary, not the queries-layer courtesy."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    _request_publish(scoped, workspace_id)

    with pytest.raises(UniqueViolation), client.app.state.job_db.connect() as conn:
        conn.execute(
            "insert into studio_publish_requests(id, workspace_id, expires_at)"
            " values ('direct-insert-1', %s, now() + interval '1 minute'),"
            " ('direct-insert-2', %s, now() + interval '1 minute')",
            (workspace_id, workspace_id),
        )


def test_cancel_during_confirm_cannot_reject_a_live_revision(client, job_db, monkeypatch) -> None:
    """P1-2: the user cancels while the confirm's publish is in flight. The
    claim already moved the row to ``confirming`` — cancel's pending-only
    predicate cannot touch it (404), and the publish completes: the row ends
    ``confirmed`` with the revision live. The old race landed ``rejected``
    on a row whose revision went live anyway."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def publish_then_cancel(job_db_, workspace_id_, yaml, enabled):
        # The cancel fires mid-publish (after the claim, before the resolve):
        # the row is ``confirming``, so the cancel must 404 — not reject.
        cancelled = client.post(
            f"/api/workspaces/{workspace_id_}/workflow-drafts/publish-request/{request_id}/cancel"
        )
        assert cancelled.status_code == 404, cancelled.text
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_then_cancel)

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )

    assert confirmed.status_code == 200, confirmed.text
    resolved = confirmed.json()["request"]
    assert resolved["status"] == "confirmed"
    assert resolved["result_revision_id"]
    # The revision is live and the agent sees the truth.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "confirmed"


def test_cancel_landing_after_the_claim_404s_not_a_200_confirming_row(
    client, job_db, monkeypatch
) -> None:
    """#429 终局 P3: the interleave the docstring promised but the old code
    missed — cancel's pre-read saw the row ``pending``, then the confirm's
    claim landed before cancel's UPDATE. The rejected-write matches no row;
    the old read-back answered the LIVE ``confirming`` row as a 200, a
    non-terminal response masquerading as a resolution (and the frontend's
    markResolved would suppress the real receipt). The contract answer is
    404: the publish is in flight and will record its own outcome."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def claim_then_delayed_cancel(job_db_, workspace_id_, yaml, enabled):
        # The claim has landed (the row is ``confirming``) when the cancel
        # fires: it hits the read-back path, not the expiry pre-check —
        # exactly the interleave the fix targets. Old behavior answered
        # this 200 with a confirming row; the contract says 404.
        cancelled = client.post(
            f"/api/workspaces/{workspace_id_}/workflow-drafts/publish-request/{request_id}/cancel"
        )
        assert cancelled.status_code == 404, cancelled.text
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", claim_then_delayed_cancel)

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "confirmed"


def test_new_request_during_confirm_window_is_refused(client, job_db, monkeypatch) -> None:
    """P1-2 (re-request semantics): a new agent request while the row is
    ``confirming`` gets 409 — superseding a confirming row would recreate the
    cancel race in supersede form. The window is one publish call; once the
    request resolves, re-requests work again."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def publish_then_re_request(job_db_, workspace_id_, yaml, enabled):
        racing = _request_publish(scoped, workspace_id_)
        assert racing.status_code == 409, racing.text
        assert "being confirmed" in racing.json()["detail"]
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_then_re_request)

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "confirmed"
    # The confirm window closed: re-requests work again (no confirming row).
    followup = _request_publish(scoped, workspace_id)
    assert followup.status_code == 200, followup.text


def test_confirm_after_draft_changed_is_refused_409(client, job_db) -> None:
    """P1-3: the draft store moved after the agent's request (canvas autosave
    with a different definition). The confirm must refuse with 409 — the
    human reviewed the request-time draft, publishing the newer one unreviewed
    is exactly the review/publish skew this fixes — and the request stays
    pending for a fresh agent request."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # The canvas saves a DIFFERENT (still-valid) definition after the request.
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 更晚的节点\n")

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 409, confirmed.text
    assert "Draft changed" in confirmed.json()["detail"]
    # No revision was published and the request is still pending (cancellable).
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 1
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]
    # A re-request against the CURRENT draft rebinds the hash and confirms.
    fresh = _request_publish(scoped, workspace_id).json()["request"]
    assert fresh["id"] != request["id"]
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{fresh['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert "更晚的节点" in active.json()["definition_yaml"]


def test_request_records_draft_hash_and_unchanged_draft_confirms(client, job_db) -> None:
    """P1-3 (the happy path of the binding): the parked request records the
    server draft's hash; a confirm against the UNCHANGED draft succeeds."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # The hash is a plain sha256 hex digest of the server draft YAML.
    import hashlib

    draft = client.get(f"/api/workspaces/{workspace_id}/workflow-draft").json()
    expected = hashlib.sha256(draft["definition_yaml"].encode("utf-8")).hexdigest()
    assert request["draft_hash"] == expected

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "confirmed"


def test_refused_publish_returns_request_to_pending(client, job_db) -> None:
    """P1-2 (claim rollback): a confirm whose publish is refused (draft
    invalidated after the request — e.g. a node's code unpublished) must move
    the row BACK to pending, not leave it confirming (uncancellable dead end)
    and not resolve it."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # Invalidate the draft between request and confirm: a new node with no
    # published node code fails the publish validation set.
    _put_draft(
        client,
        workspace_id,
        _DRAFT_YAML + "  brand_new_node:\n    capability: brand_new_capability\n",
    )
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )
    assert confirmed.status_code == 409, confirmed.text

    # Back to pending: the poll surfaces it (fixable / cancellable), and the
    # cancel path works again (the rollback really re-opened the request).
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]
    cancelled = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/cancel"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["request"]["status"] == "rejected"


def test_non_service_exception_rolls_back_to_pending_not_confirming(
    client, job_db, monkeypatch
) -> None:
    """#429 四轮 P1: the confirm's publish pipeline raises something that is
    NOT a JobServiceError (e.g. a DB drop surfacing as a bare exception).
    The row must still roll back to pending — a confirming row left behind is
    invisible to the poll, untouchable by cancel, and wedges every later
    request behind the confirming guard. The 500 is the caller's answer; the
    row state is the recovery contract."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    from server.app.services import studio_publish_requests as service_module

    def publish_raises(job_db_, workspace_id_, yaml, enabled):
        raise RuntimeError("simulated infrastructure failure mid-publish")

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_raises)

    # The TestClient re-raises unhandled server exceptions (the production
    # process would answer 500 through the error middleware); the exception
    # surfacing here IS the 500. The row state below is the recovery contract.
    with pytest.raises(RuntimeError, match="simulated infrastructure failure"):
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
        )

    # Pending again, not confirming: the poll surfaces it and the user can
    # retry or cancel (no dead end, no wedged workspace).
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]
    cancelled = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/cancel"
    )
    assert cancelled.status_code == 200


def test_stale_confirming_row_recovers_via_sweep(client, job_db, monkeypatch) -> None:
    """#429 四轮 P1: the process died between claim and resolve (deploy
    restart) — the row sits ``confirming`` past the stale threshold. The
    sweep (ridden by the pending poll / the create guard) must flip it to
    ``expired`` so (a) the agent's status tool sees an honest terminal state
    and (b) a NEW request can be parked instead of 409ing forever."""
    from server.app.services.studio_publish_request_support import (
        CONFIRMING_STALE_SECONDS,
    )

    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]
    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft
    job_db_local = client.app.state.job_db

    def claim_then_die(job_db_, workspace_id_, yaml, enabled):
        # Simulate the process dying between claim and resolve: age the claim
        # past the threshold, publish (the effect is real and on disk — keeps
        # the draft's post-publish state coherent with the follow-up request
        # below), then die — the resolve never runs.
        with job_db_.connect() as conn:
            conn.execute(
                "update studio_publish_requests set claimed_at = current_timestamp"
                f" - interval '{int(CONFIRMING_STALE_SECONDS) + 60} seconds'"
                " where id=%s",
                (request["id"],),
            )
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    def resolve_never_runs(*args, **kwargs):
        # The dead process's resolve: no-op (None = lost race).
        return None

    monkeypatch.setattr(service_module, "publish_workflow_draft", claim_then_die)
    monkeypatch.setattr(job_db_local, "resolve_publish_request", resolve_never_runs)
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    monkeypatch.undo()
    assert confirmed.status_code == 200, confirmed.text
    # The publish landed but the row is stuck confirming (aged past TTL):
    # the resolve reported the lost race through the final-state read.
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "confirming"

    # The pending poll sweeps the stale confirming row away; the agent's
    # status tool sees the honest terminal state.
    assert _pending(client, workspace_id).json()["request"] is None
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "expired"
    # And the workspace is unwedged: a fresh request can be parked.
    followup = _request_publish(scoped, workspace_id)
    assert followup.status_code == 200, followup.text


def test_fresh_confirming_row_is_not_swept_by_the_poll(client, job_db) -> None:
    """The sweep's threshold discipline (#429 四轮 P1): a confirming row
    whose claim is FRESH (a live publish, even a slow one) is never expired —
    only rows past CONFIRMING_STALE_SECONDS are. A live claim surfaces to
    the poll as the in-flight confirm (#429 四轮 P3-2) and stays intact."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    # Park a confirming row the way a live confirm does; the poll surfaces
    # the fresh confirming row (P3-2) — NOT swept: live claims stay intact.
    job_db.claim_pending_publish_request(workspace_id, request_id, request["draft_hash"])
    polled = _pending(client, workspace_id).json()["request"]
    assert polled is not None
    assert polled["id"] == request_id
    assert polled["status"] == "confirming"
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "confirming"
    # The create guard still refuses while the claim is live.
    racing = _request_publish(scoped, workspace_id)
    assert racing.status_code == 409, racing.text


def test_concurrent_create_during_claim_never_parks_a_second_request(
    client, job_db, monkeypatch
) -> None:
    """#429 四轮 codex P1: the confirming guard and the INSERT share one
    transaction serialized against the claim by the workspace advisory lock.
    A create racing a live claim is refused (409) — it can never observe
    "no confirming" in the gap between the claim's UPDATE and its commit and
    park a second pending row into the publish window (which would surface a
    second review dialog on the same stale draft after the first publish
    resolves)."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]
    confirm_url = (
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def claim_then_concurrent_create(job_db_, workspace_id_, yaml, enabled):
        # Mid-publish (the row is ``confirming``): the agent re-requests,
        # concurrently. The old two-transaction guard could pass here; the
        # in-transaction guard must refuse.
        racing = _request_publish(scoped, workspace_id_)
        assert racing.status_code == 409, racing.text
        assert "being confirmed" in racing.json()["detail"]
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", claim_then_concurrent_create)
    confirmed = client.post(confirm_url)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "confirmed"

    # After the window closes, re-requests work again — and the workspace
    # never held two pending rows at once.
    with client.app.state.job_db.connect() as conn:
        pendings = conn.execute(
            "select count(*) from studio_publish_requests"
            " where workspace_id=%s and status in ('pending', 'confirming')",
            (workspace_id,),
        ).fetchone()
    assert pendings["count"] == 0
    followup = _request_publish(scoped, workspace_id)
    assert followup.status_code == 200, followup.text


# -- #429 四轮 P3-2: confirming rows keep the dialog up during the poll ----


def test_pending_poll_surfaces_the_confirming_row_while_publish_is_in_flight(
    client, job_db, monkeypatch
) -> None:
    """P3-2: a confirm that outlives one 5s poll cycle. The poll must return
    the ``confirming`` row (status and all) instead of null — the dialog
    stays open showing the publish in progress, and the frontend's
    pending→null observer does not fire a bogus "resolved away" receipt
    mid-publish."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]
    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def publish_then_poll(job_db_, workspace_id_, yaml, enabled):
        # Mid-publish (after the claim, before the resolve): the poll runs
        # and must see the confirming row, not null.
        polled = _pending(client, workspace_id_).json()["request"]
        assert polled is not None
        assert polled["id"] == request_id
        assert polled["status"] == "confirming"
        return real_publish(job_db_, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_then_poll)
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    # Once resolved, the poll is empty again.
    assert _pending(client, workspace_id).json()["request"] is None
