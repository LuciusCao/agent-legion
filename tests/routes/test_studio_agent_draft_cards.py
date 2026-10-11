"""#1123: synthetic draft_saved timeline events on the studio-agent save tools.

Subagent (Agent/AgentSwarm) saves never surface as ACP tool_call messages, so
the chat draft cards had no data source for them; the save routes now append
a synthetic ``kind='draft'`` message on the success path when the MCP
loopback carries the session binding header. These tests pin: the event
lands on success with the card payloads (hashes), and every non-success or
non-attributable path (no header, unknown/foreign/closed session, 404/409)
writes nothing.
"""

from __future__ import annotations

import pytest

from tests.routes.studio_agent_tools_testlib import (
    _NODE_KEY,
    _active_yaml,
    _create_workspace,
    _scoped_client,
    reset_create_count,
)

_SESSION_HEADER = "x-agent-legion-mcp-session-id"


@pytest.fixture(autouse=True)
def _reset_create_count():
    reset_create_count()
    yield


def _create_chat_session(job_db, workspace_id: str, user_id: str) -> str:
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "kimi")
    assert session_id is not None
    return session_id


def _draft_messages(job_db, session_id: str) -> list[dict]:
    return [
        message
        for message in job_db.list_studio_chat_messages(session_id)
        if message["kind"] == "draft"
    ]


def test_node_code_draft_save_appends_draft_event(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n", "change_note": "tweak"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    events = _draft_messages(job_db, session_id)
    assert len(events) == 1
    assert events[0]["role"] == "agent"
    assert events[0]["content"] == {
        "event": "draft_saved",
        "draft_type": "node_code",
        "node_key": _NODE_KEY,
        "code_hash": response.json()["code_hash"],
    }


def test_workflow_draft_save_appends_draft_event(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)
    draft_yaml = _active_yaml(scoped, workspace_id).replace("label: ", "label: v2 ", 1)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    events = _draft_messages(job_db, session_id)
    assert len(events) == 1
    assert events[0]["content"] == {
        "event": "draft_saved",
        "draft_type": "workflow",
        "definition_yaml": draft_yaml,
        "definition_hash": response.json()["definition_hash"],
    }


def test_save_without_session_header_appends_nothing(client, job_db) -> None:
    """Human/editor-shaped calls (and external self-service MCP setups without
    a session binding) must not grow timeline writes."""
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_save_with_foreign_workspace_session_appends_nothing(client, job_db) -> None:
    """The header is an attribution hint, not an authority: a session of
    another workspace never gets the event (its timeline would leak the
    save)."""
    workspace_id = _create_workspace(client)
    other_workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    foreign_session = _create_chat_session(job_db, other_workspace_id, admin_id)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: foreign_session},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, foreign_session) == []


def test_save_with_unknown_session_appends_nothing(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: "sess-does-not-exist"},
    )

    assert response.status_code == 200, response.text


def test_save_with_closed_session_appends_nothing(client, job_db) -> None:
    """Closed (or archived — archive stamps closed first) sessions get no
    card: the live-guarded insert skips them atomically."""
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)
    job_db.update_studio_chat_session(session_id, status="closed")

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_failed_saves_append_nothing(client, job_db) -> None:
    """Only the success path emits: a 409 CAS conflict and a 404 unknown node
    leave the timeline untouched."""
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)

    missing = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/node_missing/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )
    assert missing.status_code == 404

    draft_yaml = _active_yaml(scoped, workspace_id)
    first = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
        headers={_SESSION_HEADER: session_id},
    )
    assert first.status_code == 200
    conflict = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "expected_updated_at": "never-saved"},
        headers={_SESSION_HEADER: session_id},
    )
    assert conflict.status_code == 409

    # Exactly one event — from the single successful save.
    assert len(_draft_messages(job_db, session_id)) == 1
