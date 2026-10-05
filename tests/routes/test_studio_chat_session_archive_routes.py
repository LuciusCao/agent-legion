"""Studio chat session archive routes (#924): POST .../archive and
POST .../unarchive, plus the ``archived=true`` archive view of the list.

Contract pinned here:
- archive closes the live runtime (scoped run token revoked) and hides the
  session from the default list; ``GET .../sessions`` with ``archived=true`` lists it;
  the session itself stays readable (GET / messages 200: hidden, not gone);
- resume of an archived session answers 409 until it is unarchived;
  unarchive puts it back in the default list, still closed, with no runtime
  spawned — the existing resume path then continues it;
- delete stays available for archived sessions and removes them from the
  archive view too;
- both endpoints are effecting: scoped studio-agent tokens get 403
  (STUDIO-AGENT-001); non-members 404; viewers and editors 403 (Studio chat
  is admin-only authoring, P4); anonymous 401 — same matrix as #872.
"""

from __future__ import annotations

import pytest

from tests.helpers.studio_chat_session_routes import (
    close_created_sessions,
    create_session,
    create_workspace,
    list_ids,
    member_client,
    register_fake_agent,
    scoped_headers,
)
from tests.helpers.studio_chat_session_routes import (
    url as session_url,
)


@pytest.fixture(autouse=True)
def _close_created_sessions(client):
    yield
    close_created_sessions(client)


def _archived_ids(client, workspace_id: str) -> list[str]:
    response = client.get(
        f"/api/workspaces/{workspace_id}/studio-chat/sessions", params={"archived": "true"}
    )
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["sessions"]]


def test_archive_hides_session_closes_runtime_and_unarchive_restores(client, tmp_path) -> None:
    script_path = register_fake_agent(client, tmp_path)
    workspace_id = create_workspace(client)
    keep_id = create_session(client, workspace_id, "keep")
    session_id = create_session(client, workspace_id, "shelve")
    url = session_url(workspace_id, session_id)
    token_headers = scoped_headers(script_path)
    tools_url = f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active"
    assert client.get(tools_url, headers=token_headers).status_code != 401

    archived = client.post(f"{url}/archive")
    assert archived.status_code == 200, archived.text
    body = archived.json()["session"]
    assert body["status"] == "closed"
    assert body["archived_at"] is not None
    # The runtime was closed first-class: its run token is revoked.
    assert client.get(tools_url, headers=token_headers).status_code == 401
    assert client.app.state.studio_chat_service.runtime(session_id) is None

    # Default list hides it; the archive view shows it; it stays readable.
    assert list_ids(client, workspace_id) == [keep_id]
    assert _archived_ids(client, workspace_id) == [session_id]
    assert client.get(url).json()["session"]["archived_at"] is not None
    assert client.get(f"{url}/messages").status_code == 200
    # Archiving again is an idempotent no-op.
    assert client.post(f"{url}/archive").status_code == 200

    # An archived session must be unarchived before it can continue.
    resumed = client.post(f"{url}/resume")
    assert resumed.status_code == 409, resumed.text
    assert "archived" in resumed.json()["detail"]

    restored = client.post(f"{url}/unarchive")
    assert restored.status_code == 200, restored.text
    assert restored.json()["session"]["archived_at"] is None
    # Unarchive never spawns a runtime: still closed, back in the list.
    assert restored.json()["session"]["status"] == "closed"
    assert client.app.state.studio_chat_service.runtime(session_id) is None
    assert list_ids(client, workspace_id) == [session_id, keep_id]
    assert _archived_ids(client, workspace_id) == []
    # The existing "continue" path brings it back.
    assert client.post(f"{url}/resume").json()["session"]["status"] == "idle"


def test_archived_session_can_still_be_deleted(client, tmp_path) -> None:
    register_fake_agent(client, tmp_path)
    workspace_id = create_workspace(client)
    session_id = create_session(client, workspace_id)
    url = session_url(workspace_id, session_id)
    assert client.post(f"{url}/archive").status_code == 200
    assert client.post(f"{url}/delete").status_code == 200
    assert _archived_ids(client, workspace_id) == []
    assert list_ids(client, workspace_id) == []
    assert client.post(f"{url}/archive").status_code == 404
    assert client.post(f"{url}/unarchive").status_code == 404


def test_archive_unknown_and_cross_workspace_is_404(client, tmp_path) -> None:
    register_fake_agent(client, tmp_path)
    workspace_a = create_workspace(client, "_a")
    workspace_b = create_workspace(client, "_b")
    session_id = create_session(client, workspace_a)
    for action in ("archive", "unarchive"):
        assert client.post(f"{session_url(workspace_a, 'missing')}/{action}").status_code == 404
        assert client.post(f"{session_url(workspace_b, session_id)}/{action}").status_code == 404
    assert list_ids(client, workspace_a) == [session_id]


def test_scoped_token_cannot_archive_or_unarchive(client, tmp_path) -> None:
    """STUDIO-AGENT-001: the session's own agent token must not archive (and
    thereby close) its chat, nor unarchive one."""
    script_path = register_fake_agent(client, tmp_path)
    workspace_id = create_workspace(client)
    session_id = create_session(client, workspace_id)
    url = session_url(workspace_id, session_id)
    scoped = scoped_headers(script_path)
    assert client.post(f"{url}/archive", headers=scoped).status_code == 403
    assert client.post(f"{url}/unarchive", headers=scoped).status_code == 403
    assert list_ids(client, workspace_id) == [session_id]
    assert client.get(url).json()["session"]["status"] == "idle"


@pytest.mark.parametrize(
    ("role", "expected"),
    [(None, 404), ("viewer", 403), ("editor", 403)],
)
def test_member_role_matrix(client, job_db, tmp_path, role: str | None, expected: int) -> None:
    register_fake_agent(client, tmp_path)
    workspace_id = create_workspace(client)
    session_id = create_session(client, workspace_id)
    url = session_url(workspace_id, session_id)
    member, member_id = member_client(client, f"archive-{role or 'outsider'}")
    if role is not None:
        job_db.upsert_workspace_member(workspace_id, member_id, role)
    assert member.post(f"{url}/archive").status_code == expected
    assert list_ids(client, workspace_id) == [session_id]
    # Unarchive is gated the same way (archived by the admin first).
    assert client.post(f"{url}/archive").status_code == 200
    assert member.post(f"{url}/unarchive").status_code == expected
    archived_view = f"/api/workspaces/{workspace_id}/studio-chat/sessions"
    assert member.get(archived_view, params={"archived": "true"}).status_code == expected
    assert _archived_ids(client, workspace_id) == [session_id]


def test_anonymous_archive_and_unarchive_are_401(anon_client) -> None:
    url = "/api/workspaces/ws-1/studio-chat/sessions/s-1"
    assert anon_client.post(f"{url}/archive").status_code == 401
    assert anon_client.post(f"{url}/unarchive").status_code == 401
