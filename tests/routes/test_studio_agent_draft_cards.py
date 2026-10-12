"""#1123: synthetic draft_saved timeline events on the studio-agent save tools.

Subagent (Agent/AgentSwarm) saves never surface as ACP tool_call messages, so
the chat draft cards had no data source for them; the save routes now append
a synthetic ``kind='draft'`` message on the success path when the MCP
loopback carries the session binding header. These tests pin: the event
lands on success with the card payloads (hashes); the token↔session binding
is enforced (a token may only card its OWN session — codex P2); and every
non-success or non-attributable path (no header, unknown/foreign/closed
session, runtime-less token, 404/409) writes nothing.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from server.app.auth import scoped_tokens
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.runtime import SessionRuntime
from tests.routes.studio_agent_tools_testlib import (
    _NODE_KEY,
    _active_yaml,
    _create_workspace,
    reset_create_count,
)

_SESSION_HEADER = "x-agent-legion-mcp-session-id"


@pytest.fixture(autouse=True)
def _reset_create_count():
    reset_create_count()
    yield


@pytest.fixture
def registered_runtimes(client):
    """Track runtimes registered on the shared app's chat service so tests
    leave no residue for the next test (the app is session-scoped)."""
    service = client.app.state.studio_chat_service
    registered: list[str] = []
    yield service, registered
    for session_id in registered:
        service._runtimes.pop(session_id, None)


def _mint_bound_session(client, job_db, workspace_id: str, admin_id: str, registered: list[str]):
    """A scoped client whose token is the session's own run token: mint the
    token, then register a runtime holding it (the spawn-time binding,
    tests/helpers/studio_chat_fixtures.py 同款形状)."""
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    session_id = job_db.create_studio_chat_session(workspace_id, admin_id, "kimi")
    assert session_id is not None
    handle = AcpSessionHandle(
        command="unused", args=[], cwd="/tmp", mcp_server=None, env=None, callbacks=Mock()
    )
    client.app.state.studio_chat_service._runtimes[session_id] = SessionRuntime(handle, token)
    registered.append(session_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, session_id


def _unbound_client(client, job_db):
    """A scoped token with NO runtime binding (self-service shape): the
    conservative no-card path."""
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


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


def test_node_code_draft_save_appends_draft_event(client, job_db, registered_runtimes) -> None:
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )

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


def test_workflow_draft_save_appends_draft_event(client, job_db, registered_runtimes) -> None:
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    draft_yaml = _active_yaml(scoped, workspace_id).replace("label: ", "label: v2 ", 1)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "base_generation": 0},
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


def test_save_with_other_sessions_token_writes_no_card(client, job_db, registered_runtimes) -> None:
    """codex P2: the header session must OWN the presented run token — a
    token minted for session A cannot card session B's timeline in the same
    workspace. The save itself still succeeds (the card is notification)."""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped_a, session_a = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    _, session_b = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )

    response = scoped_a.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_b},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_b) == []
    assert _draft_messages(job_db, session_a) == []


def test_save_without_session_header_appends_nothing(client, job_db, registered_runtimes) -> None:
    """Human/editor-shaped calls (and external self-service MCP setups without
    a session binding) must not grow timeline writes."""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_runtime_less_token_gets_no_card_even_with_own_session_header(client, job_db) -> None:
    """保守行为：无 runtime 绑定的 token（self-service origin='user' 形态）
    指到同 workspace 的 live session 也不写卡——绑定关系查不到即不写。"""
    workspace_id = _create_workspace(client)
    scoped, admin_id = _unbound_client(client, job_db)
    session_id = _create_chat_session(job_db, workspace_id, admin_id)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_save_with_foreign_workspace_session_appends_nothing(
    client, job_db, registered_runtimes
) -> None:
    """The header is an attribution hint, not an authority: a session of
    another workspace never gets the event (its timeline would leak the
    save)."""
    workspace_id = _create_workspace(client)
    other_workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, _ = _mint_bound_session(client, job_db, workspace_id, admin_id, registered_runtimes[1])
    foreign_session = _create_chat_session(job_db, other_workspace_id, admin_id)
    # Register a runtime holding the SAME token under the foreign session:
    # even a forged binding shape fails the workspace cross-check.
    handle = AcpSessionHandle(
        command="unused", args=[], cwd="/tmp", mcp_server=None, env=None, callbacks=Mock()
    )
    token = scoped.headers["authorization"].removeprefix("Bearer ")
    client.app.state.studio_chat_service._runtimes[foreign_session] = SessionRuntime(handle, token)
    registered_runtimes[1].append(foreign_session)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: foreign_session},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, foreign_session) == []


def test_save_with_unknown_session_appends_nothing(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _unbound_client(client, job_db)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: "sess-does-not-exist"},
    )

    assert response.status_code == 200, response.text


def test_save_with_closed_session_appends_nothing(client, job_db, registered_runtimes) -> None:
    """Closed (or archived — archive stamps closed first) sessions get no
    card: the live-guarded insert skips them atomically."""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    job_db.update_studio_chat_session(session_id, status="closed")

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_archive_stamp_window_gets_no_card(client, job_db, registered_runtimes) -> None:
    """#1123 codex P2-c：归档先盖 archived_at、后翻 closed——盖章即拒写，
    戳→关闭窗口内的保存不得落卡（共享 append_message_if_live 同谓词）。"""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    job_db.set_studio_chat_session_archived(session_id, True)
    # 窗口形态：归档戳已落、状态仍是 live（关闭尚未完成）。
    assert job_db.get_studio_chat_session(session_id)["status"] == "starting"

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []
    # 族修复同谓词：共享 append_message_if_live 也拒已归档会话。
    service = client.app.state.studio_chat_service
    assert (
        service.store.append_message_if_live(
            session_id, "status", "system", {"event": "mcp_callback_unreachable"}, ("starting",)
        )
        is None
    )


def test_stale_run_token_gets_no_card_after_runtime_swap(
    client, job_db, registered_runtimes
) -> None:
    """#1123 codex P2-a：resume/重铸后旧 run token（仍有效、同 workspace）
    不得写入新代次会话的时间线——绑定按 registry 当前 runtime 校验。"""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    # Resume 形态：registry 换上持新 token 的新 runtime（旧 token 未吊销）。
    new_token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    handle = AcpSessionHandle(
        command="unused", args=[], cwd="/tmp", mcp_server=None, env=None, callbacks=Mock()
    )
    client.app.state.studio_chat_service._runtimes[session_id] = SessionRuntime(handle, new_token)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )

    assert response.status_code == 200, response.text
    assert _draft_messages(job_db, session_id) == []


def test_emit_holds_runtime_registry_lock_across_guarded_insert(
    client, job_db, registered_runtimes, monkeypatch
) -> None:
    """#1123 codex P2-a：token 校验与受保护 INSERT 在同一把 registry 锁内
    （close/resume 的代次切换都在这把锁下）。故障注入：INSERT 阻塞期间
    registry 锁必须被持有——这是确定性断言（insert 只有主线程放行后才返回，
    探测时 emit 必然仍在临界区内），不是时序试探。"""
    import threading

    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    service = client.app.state.studio_chat_service
    entered = threading.Event()
    release = threading.Event()
    original = service.db.append_studio_chat_node_code_draft_event

    def blocking_insert(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=30)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.db, "append_studio_chat_node_code_draft_event", blocking_insert)
    responses: list = []
    saver = threading.Thread(
        target=lambda: responses.append(
            scoped.put(
                f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft",
                json={"code": "def run(ctx):\n    return {}\n"},
                headers={_SESSION_HEADER: session_id},
            )
        )
    )
    saver.start()
    assert entered.wait(timeout=30)
    # emit 持有 registry 锁横穿受保护 INSERT：非阻塞获取必然失败。
    # （探测成功也必须立刻释放——失败形态下不留锁残渣拖死 teardown。）
    probed = service._runtimes_lock.acquire(blocking=False)
    if probed:
        service._runtimes_lock.release()
    release.set()
    saver.join(timeout=30)
    assert not probed
    assert responses[0].status_code == 200
    assert len(_draft_messages(job_db, session_id)) == 1
    assert service._runtimes_lock.acquire(blocking=False)
    service._runtimes_lock.release()


def test_stalled_node_code_save_event_is_dropped_after_overwrite(
    client, job_db, registered_runtimes
) -> None:
    """#1123 codex P2-b：并发保存同一节点——A 先提交草稿后停滞，B 覆盖并
    落事件，A 的迟到事件被草稿代次闸（row id + code_hash）丢弃，时间线
    顺序即权威草稿写入顺序。"""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    url = f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/{_NODE_KEY}/code/draft"

    save_a = scoped.put(
        url,
        json={"code": "def run(ctx):\n    return {'v': 'a'}\n"},
        headers={_SESSION_HEADER: session_id},
    )
    assert save_a.status_code == 200
    row_a = save_a.json()
    save_b = scoped.put(
        url,
        json={"code": "def run(ctx):\n    return {'v': 'b'}\n"},
        headers={_SESSION_HEADER: session_id},
    )
    assert save_b.status_code == 200

    # A 的保存响应在 B 落库后才走到事件写入（post-commit 停滞形态）。
    from server.app.studio_chat import draft_events

    token = scoped.headers["authorization"].removeprefix("Bearer ")
    draft_events.emit_node_code_draft_saved(
        client.app.state.studio_chat_service, session_id, token, workspace_id, _NODE_KEY, row_a
    )

    events = _draft_messages(job_db, session_id)
    assert [event["content"]["code_hash"] for event in events] == [
        row_a["code_hash"],
        save_b.json()["code_hash"],
    ]
    # A 的迟到事件没有第三条：时间线只认当前草稿。
    assert len(events) == 2


def test_stalled_workflow_save_event_is_dropped_after_overwrite(
    client, job_db, registered_runtimes
) -> None:
    """#1123 codex P2-b（workflow 族）：迟到事件被 updated_at 代次闸丢弃。"""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )
    url = f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft"
    yaml_a = _active_yaml(scoped, workspace_id)

    save_a = scoped.put(
        url,
        json={"definition_yaml": yaml_a, "base_generation": 0},
        headers={_SESSION_HEADER: session_id},
    )
    assert save_a.status_code == 200
    yaml_b = yaml_a.replace("label: ", "label: v2 ", 1)
    save_b = scoped.put(
        url,
        json={
            "definition_yaml": yaml_b,
            "base_generation": save_a.json()["generation"],
        },
        headers={_SESSION_HEADER: session_id},
    )
    assert save_b.status_code == 200

    from server.app.studio_chat import draft_events

    token = scoped.headers["authorization"].removeprefix("Bearer ")
    draft_events.emit_workflow_draft_saved(
        client.app.state.studio_chat_service,
        session_id,
        token,
        workspace_id,
        {
            "definition_yaml": yaml_a,
            "definition_hash": save_a.json()["definition_hash"],
            "updated_at": save_a.json()["updated_at"],
        },
    )

    events = _draft_messages(job_db, session_id)
    assert len(events) == 2
    assert events[-1]["content"]["definition_yaml"] == yaml_b


def test_failed_saves_append_nothing(client, job_db, registered_runtimes) -> None:
    """Only the success path emits: a 409 CAS conflict and a 404 unknown node
    leave the timeline untouched."""
    workspace_id = _create_workspace(client)
    _, admin_id = _unbound_client(client, job_db)
    scoped, session_id = _mint_bound_session(
        client, job_db, workspace_id, admin_id, registered_runtimes[1]
    )

    missing = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/nodes/node_missing/code/draft",
        json={"code": "def run(ctx):\n    return {}\n"},
        headers={_SESSION_HEADER: session_id},
    )
    assert missing.status_code == 404

    draft_yaml = _active_yaml(scoped, workspace_id)
    first = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "base_generation": 0},
        headers={_SESSION_HEADER: session_id},
    )
    assert first.status_code == 200
    conflict = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/draft",
        json={"definition_yaml": draft_yaml, "base_generation": 0},
        headers={_SESSION_HEADER: session_id},
    )
    assert conflict.status_code == 409

    # Exactly one event — from the single successful save.
    assert len(_draft_messages(job_db, session_id)) == 1
