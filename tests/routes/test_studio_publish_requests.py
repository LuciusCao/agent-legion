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

from fastapi.testclient import TestClient

from server.app.auth import scoped_tokens
from server.app.services.node_codes import NodeCodeService

_DRAFT_YAML = """
key: publish_flow_ws
label: Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""


def _seed_workspace(client: TestClient, job_db, name: str = "publish_flow_ws") -> str:
    """Create a workspace via the API (id == key, v62) and publish v1 so the
    draft has a resolvable baseline + node code."""
    response = client.post("/api/workspaces", json={"id": name, "name": "Publish WS"})
    assert response.status_code == 200, response.text
    workspace_id = str(response.json()["workspace"]["id"])
    codes = NodeCodeService(job_db.dsn_identity)
    codes.save_draft(
        workspace_id,
        name,
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, name, "do_thing")
    yaml = (
        _DRAFT_YAML if name == "publish_flow_ws" else _DRAFT_YAML.replace("publish_flow_ws", name)
    )
    published = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
        json={"definition_yaml": yaml},
    )
    assert published.status_code == 200 and published.json()["valid"], published.text
    return workspace_id


def _scoped_client(client, job_db, workspace_id: str | None = None) -> TestClient:
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def _put_draft(client: TestClient, workspace_id: str, yaml: str) -> None:
    """Seed the workspace draft store (the YAML confirm publishes)."""
    response = client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": yaml},
    )
    assert response.status_code == 200, response.text


def _request_publish(scoped: TestClient, workspace_id: str):
    return scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/publish-request"
    )


def _pending(client: TestClient, workspace_id: str):
    return client.get(f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request")


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
    from the workspace default between request and confirm is a 422, and the
    request stays pending."""
    workspace_id = _seed_workspace(client, job_db)
    _put_draft(client, workspace_id, _DRAFT_YAML + "    label: 调整后的节点\n")
    scoped = _scoped_client(client, job_db, workspace_id)
    request = _request_publish(scoped, workspace_id).json()["request"]

    # The human's canvas edits the draft's key away from the workspace default
    # after the agent parked its request: confirm must fail with the same 422
    # the manual publish button produces (the key guard runs before the
    # validation set, so no node code under the foreign key is needed).
    _put_draft(client, workspace_id, _DRAFT_YAML.replace("publish_flow_ws", "foreign_flow"))
    confirmed = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request/{request['id']}/confirm"
    )

    assert confirmed.status_code == 422, confirmed.text
    assert "foreign_flow" in confirmed.json()["detail"]
    # The request survives the refused confirm: fixable, or cancellable.
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
