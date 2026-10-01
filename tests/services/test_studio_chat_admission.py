"""Adversarial admission failures must leave no undelivered conversation history."""

from unittest.mock import Mock

import pytest
from psycopg import OperationalError

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.auth.sessions import hash_token
from server.app.db.connection import DatabaseConnection
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import admission as service_module
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.token_admission import require_live_run_token
from server.app.studio_chat.token_keepalive import keepalive_run_token


@pytest.fixture
def admission(job_db, settings):
    service = StudioChatService(job_db, settings, None)
    workspace = job_db.create_workspace(default_workflow_key="demo_workflow", name="Admission")[
        "id"
    ]
    user = job_db.create_user("admission-user", password_hash=None)["id"]
    sid = job_db.create_studio_chat_session(workspace, user, "test-agent")
    job_db.update_studio_chat_session(sid, status="idle")
    handle = AcpSessionHandle(
        command="unused", args=[], cwd="/tmp", mcp_server=None, env=None, callbacks=Mock()
    )
    runtime = SessionRuntime(handle, mint_scoped_token(job_db, user, workspace_id=workspace))
    runtime.loading = True
    runtime.resume_transcript_pending = True
    runtime.stream.append("text", "previous")
    service._runtimes[sid] = runtime
    yield service, job_db, sid, workspace, runtime
    service.shutdown()


def assert_unaccepted(context):
    service, db, sid, _workspace, runtime = context
    assert db.count_studio_chat_user_messages(sid) == 0
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert runtime.resume_transcript_pending
    assert runtime.loading
    assert not runtime.turn_open
    assert runtime.stream.texts == {"text": "previous"}
    assert runtime.handle._queue.empty()


@pytest.mark.parametrize("phase", ["count", "renew", "transcript", "token", "insert", "commit"])
def test_failed_preparation_or_transaction_is_retryable(admission, monkeypatch, phase):
    service, db, sid, workspace, runtime = admission

    def fail(*args, **kwargs):
        raise OperationalError("injected admission failure")

    with monkeypatch.context() as patch:
        if phase == "count":
            patch.setattr(db, "count_studio_chat_user_messages", fail)
        elif phase == "renew":
            patch.setattr(service_module, "renew_scoped_token", fail)
        elif phase == "transcript":
            patch.setattr(service_module, "prepare_resume_prompt", fail)
        elif phase == "token":
            real = db.get_scoped_token_user
            calls = 0

            def fail_final(token):
                nonlocal calls
                calls += 1
                return fail() if calls == 2 else real(token)

            patch.setattr(db, "get_scoped_token_user", fail_final)
        elif phase == "insert":
            execute = DatabaseConnection.execute

            def fail_insert(conn, sql, params=None):
                if sql.startswith("insert into studio_chat_messages"):
                    fail()
                return execute(conn, sql, params)

            patch.setattr(DatabaseConnection, "execute", fail_insert)
        else:
            commit = DatabaseConnection.commit

            def fail_commit(conn):
                # Only the transaction containing the new message fails.
                row = conn.execute(
                    "select count(*) as n from studio_chat_messages where session_id=%s",
                    (sid,),
                ).fetchone()
                if row["n"]:
                    fail()
                return commit(conn)

            patch.setattr(DatabaseConnection, "commit", fail_commit)
        with pytest.raises(OperationalError, match="injected"):
            service.send_message(sid, workspace, "not accepted")
    assert_unaccepted(admission)
    service.send_message(sid, workspace, "retry once")
    assert db.count_studio_chat_user_messages(sid) == 1
    assert runtime.handle._queue.qsize() == 1
    assert not runtime.resume_transcript_pending


def test_revocation_at_atomic_admission_leaves_no_message(admission, monkeypatch):
    service, db, sid, workspace, runtime = admission
    accept = db.accept_studio_chat_message

    def revoke_then_accept(*args):
        db.revoke_scoped_token(hash_token(runtime.token))
        return accept(*args)

    monkeypatch.setattr(db, "accept_studio_chat_message", revoke_then_accept)
    with pytest.raises(ConflictError, match="继续对话"):
        service.send_message(sid, workspace, "rejected")
    assert db.count_studio_chat_user_messages(sid) == 0
    assert db.get_studio_chat_session(sid)["status"] == "error"
    assert runtime.resume_transcript_pending
    assert not runtime.turn_open
    # The only queued item is the graceful-stop sentinel, never a prompt.
    assert not isinstance(runtime.handle._queue.get_nowait(), str)
    assert runtime.handle._queue.empty()


def test_stopping_handle_refuses_before_durable_admission(admission):
    service, db, sid, workspace, runtime = admission
    runtime.handle.request_stop()
    with pytest.raises(ConflictError, match="not running"):
        service.send_message(sid, workspace, "after stop")
    assert db.count_studio_chat_user_messages(sid) == 0
    assert runtime.resume_transcript_pending
    assert not runtime.turn_open


def test_turn_state_precedes_queue_visibility_and_snapshot_failure_is_success(
    admission, monkeypatch
):
    service, db, sid, workspace, runtime = admission
    put = runtime.handle._queue.put

    def observe_put(prompt):
        assert not runtime.loading
        assert runtime.turn_open
        assert not runtime.resume_transcript_pending
        assert db.count_studio_chat_user_messages(sid) == 1
        return put(prompt)

    monkeypatch.setattr(runtime.handle._queue, "put", observe_put)
    monkeypatch.setattr(
        service.store, "publish_session", Mock(side_effect=OperationalError("read"))
    )
    message = service.send_message(sid, workspace, "accepted")
    assert message["content"]["text"] == "accepted"
    assert runtime.handle._queue.qsize() == 1
    monkeypatch.undo()


@pytest.mark.parametrize("path", ["human", "notification"])
def test_stale_token_result_cannot_escalate_replacement(admission, monkeypatch, path):
    service, db, sid, _workspace, runtime = admission
    replacement = SessionRuntime(Mock(), "successor-token")

    def replace_during_read(_token):
        service._runtimes[sid] = replacement
        return None

    monkeypatch.setattr(db, "get_scoped_token_user", replace_during_read)
    if path == "human":
        with pytest.raises(ConflictError, match="not running"):
            require_live_run_token(service, sid, runtime)
    else:
        keepalive_run_token(service, sid)
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert db.list_studio_chat_messages(sid) == []
    assert not runtime.token_keepalive_done
    replacement.handle.request_stop.assert_not_called()
    service._runtimes[sid] = runtime


def test_close_without_runtime_does_not_teardown_concurrent_resume(admission, monkeypatch):
    service, db, sid, workspace, runtime = admission
    service._runtimes.pop(sid)
    db.update_studio_chat_session(sid, status="error")
    successor = SessionRuntime(Mock(), "new-token")
    append = service.store.append_message

    def resume_after_close(*args, **kwargs):
        result = append(*args, **kwargs)
        service._runtimes[sid] = successor
        db.update_studio_chat_session(sid, status="idle")
        return result

    monkeypatch.setattr(service.store, "append_message", resume_after_close)
    service.close_session(sid, workspace)
    assert service.runtime(sid) is successor
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    successor.handle.close.assert_not_called()
    service._runtimes[sid] = runtime


def test_close_notification_failure_still_tears_down(admission, monkeypatch):
    service, db, sid, workspace, runtime = admission
    monkeypatch.setattr(
        service.store, "append_message", Mock(side_effect=OperationalError("notification"))
    )
    assert service.close_session(sid, workspace)["status"] == "closed"
    assert service.runtime(sid) is None
    assert runtime.closed
