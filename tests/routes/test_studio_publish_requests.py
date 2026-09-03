"""Behavioral contract for agent-initiated workflow publish requests (#416).

The handshake: the studio-agent tool surface parks a pending request (never
publishing); a full user session confirms through the same publish gates as
the manual Studio button, or cancels. Security matrix front and center:

- scoped tokens CANNOT confirm/cancel (reject_studio_agent_scope, 403) and
  cannot even read the pending-poll endpoint (it is a user-session surface);
- the agent's request tool NEVER produces a revision — only the human
  confirm does;
- confirm is gate-equivalent to the manual publish (key mismatch 422,
  validation failures refuse without resolving the request).
"""

from __future__ import annotations

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


def test_agent_request_parks_pending_never_publishes(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)

    response = _request_publish(scoped, workspace_id)

    assert response.status_code == 200, response.text
    request = response.json()["request"]
    assert request["status"] == "pending"
    assert request["workspace_id"] == workspace_id
    assert request["created_by"].startswith("studio-agent:")
    assert request["result_revision_id"] is None
    # Security invariant: the request alone produced no revision — the active
    # revision is still the seeded v1.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 1


def test_request_with_invalid_draft_is_refused_no_request_created(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    # Draft adds a node with no published node code: full publish validation
    # fails (code nodes resolve per node key, EXEC-CODE-002).
    _put_draft(
        client,
        workspace_id,
        _DRAFT_YAML + "  brand_new_node:\n    capability: brand_new_capability\n",
    )
    scoped = _scoped_client(client, job_db, workspace_id)

    response = _request_publish(scoped, workspace_id)

    assert response.status_code == 409, response.text
    assert "validation errors" in response.json()["detail"]
    assert _pending(client, workspace_id).json()["request"] is None


def test_request_without_draft_store_conflicts(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    scoped = _scoped_client(client, job_db, workspace_id)

    response = _request_publish(scoped, workspace_id)

    assert response.status_code == 409
    assert "No unpublished workflow draft" in response.json()["detail"]


def test_new_request_supersedes_the_previous_pending(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)

    first = _request_publish(scoped, workspace_id).json()["request"]
    second = _request_publish(scoped, workspace_id).json()["request"]

    assert first["id"] != second["id"]
    # The status tool reads the OLD request as superseded.
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{first['id']}")
    assert status.json()["request"]["status"] == "superseded"
    # The pending poll surfaces only the NEW one.
    assert _pending(client, workspace_id).json()["request"]["id"] == second["id"]


def test_human_confirm_publishes_and_records_revision(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 200, confirmed.text
    resolved = confirmed.json()["request"]
    assert resolved["status"] == "confirmed"
    assert resolved["result_revision_id"]
    # The revision actually exists and is active (v2 of the workspace).
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2
    assert active.json()["revision"]["id"] == resolved["result_revision_id"]
    # The agent's status tool sees the outcome.
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request['id']}")
    assert status.json()["request"]["status"] == "confirmed"
    # Pending poll is empty after resolution.
    assert _pending(client, workspace_id).json()["request"] is None


def test_human_cancel_rejects_and_keeps_draft(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    cancelled = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/cancel"
    )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["request"]["status"] == "rejected"
    assert cancelled.json()["request"]["result_revision_id"] is None
    # No revision was created; the draft store still holds the agent's YAML.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 1
    draft = client.get(f"/api/workspaces/{workspace_id}/workflow-draft")
    assert "调整后的节点" in draft.json()["definition_yaml"]
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request['id']}")
    assert status.json()["request"]["status"] == "rejected"


def test_confirm_is_gate_equivalent_key_mismatch_422(client, job_db) -> None:
    """Confirm replays the manual publish gates: a draft whose key drifted
    from the workspace default between request and confirm hits the draft
    binding first (409 — the draft changed after the request, #429 三轮
    P1-3), and the request stays pending.

    The gate-equivalence itself (same 422 the manual publish button
    produces) is pinned below against a draft whose key was ALREADY foreign
    at request time: the request tool parks the hash of that foreign-key
    draft, and the confirm replays the key-match guard unchanged."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # The human's canvas edits the draft's key away from the workspace default
    # after the agent parked its request: the draft is no longer the one the
    # request bound — refuse loudly, the request survives.
    _put_draft(client, workspace_id, _DRAFT_YAML.replace("publish_flow_ws", "foreign_flow"))
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 409, confirmed.text
    assert "Draft changed" in confirmed.json()["detail"]
    # The request survives the refused confirm: fixable, or cancellable.
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]


def test_confirm_replays_key_match_gate_422_on_unchanged_draft(client, job_db) -> None:
    """The key-match guard replayed on the UNCHANGED (hash-bound) draft: the
    draft was foreign-keyed at request time, so the request parks it; the
    confirm refuses with the same 422 the manual publish button produces
    (gate equivalence — the key guard runs before the validation set, so no
    node code under the foreign key is needed).

    The row is parked through the queries layer with the foreign draft's
    own hash: the request tool's validation set resolves node code under
    the draft's key, so a foreign-key draft cannot pass the tool — this
    pins the CONFIRM's gates, which replay the manual publish path
    regardless of how the row came to exist."""
    import hashlib

    workspace_id = _seed_workspace(client, job_db)
    foreign_yaml = _DRAFT_YAML.replace("publish_flow_ws", "foreign_flow")
    _put_draft(client, workspace_id, foreign_yaml)
    request = client.app.state.job_db.create_pending_publish_request(
        workspace_id,
        "studio-agent:test",
        draft_hash=hashlib.sha256(foreign_yaml.encode("utf-8")).hexdigest(),
    )

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 422, confirmed.text
    assert "foreign_flow" in confirmed.json()["detail"]
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]


def test_confirm_on_drifted_invalid_draft_conflicts_and_stays_pending(client, job_db) -> None:
    """The draft can change between request and confirm: a confirm whose
    draft no longer validates is refused and the request stays pending (the
    human may fix the draft and confirm again, or cancel)."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # Human edits the canvas draft into an invalid state before confirming
    # (a new node with no published code fails the publish validation set).
    _put_draft(
        client,
        workspace_id,
        _DRAFT_YAML + "  brand_new_node:\n    capability: brand_new_capability\n",
    )
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 409, confirmed.text
    # Still pending: fixable, or cancellable.
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]


def test_double_confirm_conflicts_second_call(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    url = f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    assert client.post(url).status_code == 200
    # Second confirm: the request is no longer pending.
    assert client.post(url).status_code == 404
    # Cancel after confirm likewise 404s.
    cancel_url = url.rsplit("/", 1)[0] + "/cancel"
    assert client.post(cancel_url).status_code == 404


def test_expired_request_cannot_be_confirmed(client, job_db) -> None:
    """Lazy expiry: a pending row past its TTL reads as expired (the pending
    poll is empty) and confirm 404s — the human never confirmed in time."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    # Park a short-TTL request directly through the queries layer (the tool
    # endpoint has a fixed TTL; the queries contract allows the override).
    request = client.app.state.job_db.create_pending_publish_request(
        workspace_id, "studio-agent:test", ttl_seconds=0
    )
    request_id = request["id"]

    assert _pending(client, workspace_id).json()["request"] is None
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.status_code == 200
    assert status.json()["request"]["status"] == "expired"
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )
    assert confirmed.status_code == 404


def test_confirm_survives_ttl_expiry_mid_publish(client, job_db, monkeypatch) -> None:
    """#429 TOCTOU: the TTL lapses while the publish executes — the revision
    landed, so the request MUST resolve confirmed (never 404/expired). Only
    the pre-publish claim checks TTL; the resolve predicate does not."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]
    request_id = request["id"]

    # Make the workspace's pending row look like its TTL expired between the
    # claim and the resolve (the publish runs in between): rewrite expires_at
    # to the past inside publish_workflow_draft.
    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def publish_then_expire(job_db, workspace_id_, yaml, enabled):
        with job_db.connect() as conn:
            conn.execute(
                "update studio_publish_requests set expires_at = current_timestamp - interval '1 second'"
                " where id=%s",
                (request_id,),
            )
        return real_publish(job_db, workspace_id_, yaml, enabled)

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_then_expire)

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request_id}/confirm"
    )

    assert confirmed.status_code == 200, confirmed.text
    resolved = confirmed.json()["request"]
    # The effect is real and the state machine says so.
    assert resolved["status"] == "confirmed"
    assert resolved["result_revision_id"]
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2
    # The agent polling the status tool sees the same truth (the status read
    # no longer flips a resolved row).
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request_id}")
    assert status.json()["request"]["status"] == "confirmed"


def test_confirm_resolve_loses_supersede_race_reports_final_state(
    client, job_db, monkeypatch
) -> None:
    """#429: a NEWER agent request supersedes this row while its publish is
    in flight. The publish effect happened, so the honest answer is the
    row's final state (superseded by the newer request) — not a 404 toast
    for a revision that actually exists.

    #429 三轮 P1-2 note: with the confirming claim, a new request can no
    longer supersede a row whose publish is genuinely in flight (it gets
    409) — this test drives the displacement the OLD way, rewriting the row
    directly (what a concurrent legacy writer / admin SQL would do), to pin
    the resolve-lost-race behavior that must hold regardless of how the
    row left ``confirming``."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    first = _request_publish(scoped, workspace_id).json()["request"]

    from server.app.services import studio_publish_requests as service_module

    real_publish = service_module.publish_workflow_draft

    def publish_then_supersede(job_db, workspace_id_, yaml, enabled):
        result = real_publish(job_db, workspace_id_, yaml, enabled)
        # The row leaves ``confirming`` behind the confirm's back (the direct
        # rewrite stands in for any writer the claim cannot coordinate with).
        with job_db.connect() as conn:
            conn.execute(
                "update studio_publish_requests set status='superseded',"
                " resolved_at=current_timestamp where id=%s",
                (first["id"],),
            )
        return result

    monkeypatch.setattr(service_module, "publish_workflow_draft", publish_then_supersede)

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{first['id']}/confirm"
    )

    # 200 with the truthful final state (superseded), not 404.
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["request"]["status"] == "superseded"
    # The publish effect is on disk: v2 is live.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2
    # No pending row remains (the direct rewrite took the slot out of play).
    assert _pending(client, workspace_id).json()["request"] is None


def test_manual_publish_supersedes_pending_request(client, job_db) -> None:
    """#429 P2-3: the human publishes through the toolbar button while an
    agent request is pending. The pending request is displaced (superseded),
    NOT left pending (dead-end dialog) and NOT rejected (nothing was
    refused — the content went live)."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 手动后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    manual = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
        json={"definition_yaml": _DRAFT_YAML + "    label: 手动后的节点\n"},
    )
    assert manual.status_code == 200 and manual.json()["valid"], manual.text

    # The pending poll no longer surfaces the request: no dead-end dialog.
    assert _pending(client, workspace_id).json()["request"] is None
    # The agent's status tool sees superseded — a publish happened, just not
    # through this request (vs rejected, which would claim a refusal).
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request['id']}")
    assert status.json()["request"]["status"] == "superseded"
    # And the manual publish really landed.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 2


def test_manual_publish_failure_leaves_pending_request(client, job_db) -> None:
    """The supersede only happens on a successful manual publish: a refused
    one (invalid draft) must not displace the pending request."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    manual = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
        json={"definition_yaml": _DRAFT_YAML + "  brand_new_node:\n    capability: nope\n"},
    )
    assert manual.status_code == 200 and not manual.json()["valid"]

    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]


def test_confirm_runtime_only_save_keeps_result_revision_null(client, job_db) -> None:
    """#429 P3-2: a confirm whose draft differs from the active revision only
    in runtime settings saves in place (no new version). result_revision_id
    must stay null — echoing the unchanged revision id would read as "a new
    revision exists"."""
    workspace_id = _seed_workspace(client, job_db)
    # The agent's change: runtime-only (node execution config), no
    # structural diff vs the published v1 (label etc. untouched — a label
    # change would be structural and create v2).
    _put_draft(
        client,
        workspace_id,
        _DRAFT_YAML + "    execution:\n      model: glm-4.7\n",
    )
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 200, confirmed.text
    resolved = confirmed.json()["request"]
    assert resolved["status"] == "confirmed"
    # No new revision: still v1, and the row says so with a null id.
    active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
    assert active.json()["revision"]["version"] == 1
    assert resolved["result_revision_id"] is None


def test_pending_poll_is_read_only_until_a_row_actually_expires(
    client, job_db, monkeypatch
) -> None:
    """#429 P3-1: the 5s poll's publish-request path must open no write
    connections while the pending row is healthy (auth's sliding session
    update is unrelated and always-on). Expired rows still land their
    terminal state through the poll (the route observes the expiry and only
    then writes)."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # Healthy poll: the write-capable publish-request queries must not run.
    job_db = client.app.state.job_db
    write_calls: list[str] = []
    real_expire = job_db.expire_pending_publish_request
    real_resolve = job_db.resolve_publish_request

    monkeypatch.setattr(
        job_db,
        "expire_pending_publish_request",
        lambda ws: (write_calls.append("expire"), real_expire(ws))[1],
    )
    monkeypatch.setattr(
        job_db,
        "resolve_publish_request",
        lambda *a, **k: (write_calls.append("resolve"), real_resolve(*a, **k))[1],
    )
    response = _pending(client, workspace_id)
    assert response.json()["request"]["id"] == request["id"]
    assert write_calls == []  # healthy poll: pure read, zero publish-request writes

    # Expired row: the poll observes it and records the terminal state.
    with job_db.connect() as conn:
        conn.execute(
            "update studio_publish_requests set expires_at = current_timestamp - interval '1 second'"
            " where id=%s",
            (request["id"],),
        )
    assert _pending(client, workspace_id).json()["request"] is None
    assert write_calls == ["expire"]  # observed expiry → exactly one write
    status = scoped.get(f"/api/studio-agent/tools/publish-requests/{request['id']}")
    assert status.json()["request"]["status"] == "expired"


# -- security matrix ------------------------------------------------------


def test_scoped_token_cannot_confirm_or_cancel(client, job_db) -> None:
    """STUDIO-AGENT-001: the confirm/cancel actions are user publishes. An
    agent's scoped token gets 403 on both, plus on the pending poll."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    base = f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}"
    assert scoped.post(f"{base}/confirm").status_code == 403
    assert scoped.post(f"{base}/cancel").status_code == 403
    assert (
        scoped.get(f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request").status_code
        == 403
    )
    # And the request is STILL pending — nothing the agent did took effect.
    assert _pending(client, workspace_id).json()["request"]["id"] == request["id"]


def test_anonymous_callers_get_401(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    anon = client.__class__(client.app)
    assert (
        anon.get(f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request").status_code
        == 401
    )
    assert (
        anon.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/r/confirm"
        ).status_code
        == 401
    )


def test_scoped_status_tool_respects_workspace_binding(client, job_db) -> None:
    """A run token bound to workspace A cannot read workspace B's request
    (404, not 403 — ids must not be probed across workspaces)."""
    workspace_a = _seed_workspace(client, job_db)
    workspace_b = _seed_workspace(client, job_db, "publish_flow_ws_2")
    _put_draft(client, workspace_a, _DRAFT_YAML + "    label: 调整后的节点\n")
    unbound = _scoped_client(client, job_db)
    request = _request_publish(unbound, workspace_a).json()["request"]

    bound_b = _scoped_client(client, job_db, workspace_b)
    assert (
        bound_b.get(f"/api/studio-agent/tools/publish-requests/{request['id']}").status_code == 404
    )
    bound_a = _scoped_client(client, job_db, workspace_a)
    assert (
        bound_a.get(f"/api/studio-agent/tools/publish-requests/{request['id']}").status_code == 200
    )


def test_unknown_request_ids_404(client, job_db) -> None:
    workspace_id = _seed_workspace(client, job_db)
    del job_db
    assert (
        client.get(f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request").json()[
            "request"
        ]
        is None
    )
    assert (
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/no-such-id/confirm"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/no-such-id/cancel"
        ).status_code
        == 404
    )
