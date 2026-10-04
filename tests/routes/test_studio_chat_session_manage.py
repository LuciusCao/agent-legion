"""Studio chat session management routes (#872): rename (PATCH) and soft
delete (POST .../delete).

Contract pinned here:
- PATCH renames (whitespace stripped, 200-char cap) and 404s unknown,
  cross-workspace, and deleted sessions;
- delete stamps the row, closes the live runtime (token revoked), and from
  then on the session is gone from the list and 404s on every session route
  (404 rather than 410: same answer as an unknown id);
- both endpoints are effecting: scoped studio-agent tokens get 403
  (STUDIO-AGENT-001); non-members 404; workspace viewers and editors get 403
  like every Studio chat route (Studio authoring is admin-only, P4).
"""

from __future__ import annotations

import pytest

from tests.helpers import wait_for_predicate
from tests.helpers.studio_chat_session_routes import (
    PERMISSION_SCRIPT,
    close_created_sessions,
)
from tests.helpers.studio_chat_session_routes import create_session as _create_session
from tests.helpers.studio_chat_session_routes import create_workspace as _create_workspace
from tests.helpers.studio_chat_session_routes import list_ids as _list_ids
from tests.helpers.studio_chat_session_routes import member_client as _member_client
from tests.helpers.studio_chat_session_routes import register_fake_agent as _register_fake_agent
from tests.helpers.studio_chat_session_routes import scoped_headers as _scoped_headers
from tests.helpers.studio_chat_session_routes import url as _url


@pytest.fixture(autouse=True)
def _close_created_sessions(client):
    yield
    close_created_sessions(client)


def test_rename_updates_title_list_and_detail(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _url(workspace_id, session_id)

    renamed = client.patch(url, json={"title": "  调整审核节点  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["session"]["title"] == "调整审核节点"
    assert client.get(url).json()["session"]["title"] == "调整审核节点"
    listed = client.get(f"/api/workspaces/{workspace_id}/studio-chat/sessions").json()
    assert [row["title"] for row in listed["sessions"]] == ["调整审核节点"]
    # Renaming does not touch the live runtime.
    assert client.get(url).json()["session"]["status"] == "idle"

    # Empty title is allowed (client falls back to its default label).
    assert client.patch(url, json={"title": ""}).json()["session"]["title"] == ""
    assert client.patch(url, json={"title": "x" * 201}).status_code == 422
    assert client.patch(url, json={"title": "ok", "status": "closed"}).status_code == 422


def test_rename_unknown_and_cross_workspace_is_404(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_a = _create_workspace(client, "_a")
    workspace_b = _create_workspace(client, "_b")
    session_id = _create_session(client, workspace_a)
    assert client.patch(_url(workspace_a, "missing"), json={"title": "n"}).status_code == 404
    assert client.patch(_url(workspace_b, session_id), json={"title": "n"}).status_code == 404
    assert client.post(_url(workspace_b, session_id) + "/delete").status_code == 404
    assert client.get(_url(workspace_a, session_id)).json()["session"]["title"] == "t"


def test_delete_hides_session_and_retires_runtime(client, tmp_path) -> None:
    script_path = _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    keep_id = _create_session(client, workspace_id, "keep")
    session_id = _create_session(client, workspace_id, "drop")
    url = _url(workspace_id, session_id)
    token_headers = _scoped_headers(script_path)
    tools_url = f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active"

    deleted = client.post(f"{url}/delete")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": session_id}

    assert _list_ids(client, workspace_id) == [keep_id]
    # Every session-scoped route now answers like an unknown id.
    assert client.get(url).status_code == 404
    assert client.get(f"{url}/messages").status_code == 404
    assert client.get(f"{url}/events").status_code == 404
    assert client.post(f"{url}/messages", json={"text": "hi"}).status_code == 404
    assert client.post(f"{url}/resume").status_code == 404
    assert client.patch(url, json={"title": "back"}).status_code == 404
    assert client.put(f"{url}/context", json={"selected_node_key": "n"}).status_code == 404
    assert client.delete(url).status_code == 404
    assert client.post(f"{url}/delete").status_code == 404
    # The runtime was closed: the session's scoped run token is revoked.
    assert client.get(tools_url, headers=token_headers).status_code == 401
    # The surviving session is untouched.
    assert client.get(_url(workspace_id, keep_id)).json()["session"]["status"] == "idle"


def test_delete_live_turn_closes_runtime(client, job_db, tmp_path) -> None:
    """Deleting a session mid-turn (parked on a permission prompt) closes the
    runtime first-class: the row ends closed + stamped, no runtime remains."""
    _register_fake_agent(client, tmp_path, script=PERMISSION_SCRIPT)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _url(workspace_id, session_id)
    client.post(f"{url}/messages", json={"text": "run ls"})
    wait_for_predicate(
        lambda: client.get(url).json()["session"]["status"] == "awaiting_permission",
        timeout=20.0,
        interval=0.05,
    )

    assert client.post(f"{url}/delete").status_code == 200
    row = job_db.get_studio_chat_session(session_id)
    assert row is not None
    assert row["status"] == "closed"
    assert row["deleted_at"] is not None
    service = client.app.state.studio_chat_service
    assert service.runtime(session_id) is None
    assert _list_ids(client, workspace_id) == []


def test_closed_session_can_be_deleted(client, tmp_path) -> None:
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _url(workspace_id, session_id)
    assert client.delete(url).json()["session"]["status"] == "closed"
    # Closing keeps the session listed (resumable history) ...
    assert _list_ids(client, workspace_id) == [session_id]
    # ... deleting removes it.
    assert client.post(f"{url}/delete").status_code == 200
    assert _list_ids(client, workspace_id) == []


def test_scoped_token_cannot_rename_or_delete(client, tmp_path) -> None:
    """STUDIO-AGENT-001: the session's own agent token must not rename or
    delete chats (effecting endpoints mount reject_studio_agent_scope)."""
    script_path = _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _url(workspace_id, session_id)
    scoped = _scoped_headers(script_path)
    assert client.patch(url, json={"title": "agent"}, headers=scoped).status_code == 403
    assert client.post(f"{url}/delete", headers=scoped).status_code == 403
    assert client.get(url).json()["session"]["title"] == "t"
    assert _list_ids(client, workspace_id) == [session_id]


@pytest.mark.parametrize(
    ("role", "expected"),
    [(None, 404), ("viewer", 403), ("editor", 403)],
)
def test_member_role_matrix(client, job_db, tmp_path, role: str | None, expected: int) -> None:
    """Non-members 404 (no existence signal); viewers and editors 403 — Studio
    chat is admin-only authoring (P4), so even an editor cannot rename or
    delete. Admin succeeds (the other tests)."""
    _register_fake_agent(client, tmp_path)
    workspace_id = _create_workspace(client)
    session_id = _create_session(client, workspace_id)
    url = _url(workspace_id, session_id)
    member, member_id = _member_client(client, f"chat-{role or 'outsider'}")
    if role is not None:
        job_db.upsert_workspace_member(workspace_id, member_id, role)
    assert member.patch(url, json={"title": "member"}).status_code == expected
    assert member.post(f"{url}/delete").status_code == expected
    assert client.get(url).json()["session"]["title"] == "t"
    assert _list_ids(client, workspace_id) == [session_id]


def test_anonymous_rename_and_delete_are_401(anon_client) -> None:
    url = "/api/workspaces/ws-1/studio-chat/sessions/s-1"
    assert anon_client.patch(url, json={"title": "x"}).status_code == 401
    assert anon_client.post(f"{url}/delete").status_code == 401
