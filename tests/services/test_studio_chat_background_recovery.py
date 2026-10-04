"""Recovery uses durable, ACP-scoped receipts instead of startup timing."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat import background_wakeup as wake
from server.app.studio_chat.background_recovery import recovery_sets
from server.app.studio_chat.runtime import SessionRuntime
from tests.services.studio_background_testlib import write_task

pytestmark = pytest.mark.no_db


def receipt(event="background_task_status", acp="acp-1", seq=1):
    return {
        "seq": seq,
        "kind": "status",
        "content": {"event": event, "task_id": "agent-1", "acp_session_id": acp},
    }


def test_recovery_walks_history_beyond_latest_500_messages():
    db = Mock()
    db.list_studio_chat_messages_tail.side_effect = [
        [{"seq": n, "kind": "text"} for n in range(2, 502)],
        [receipt()],
    ]
    assert recovery_sets(db, "chat-1", "acp-1", {"agent-1", "old"}) == ({"old"}, {"agent-1"})
    assert db.list_studio_chat_messages_tail.call_args.kwargs["before_seq"] == 2


@pytest.mark.parametrize("acp", ["foreign", None])
def test_foreign_and_legacy_receipts_cannot_recover_same_task_id(acp):
    db = Mock()
    db.list_studio_chat_messages_tail.return_value = [receipt(acp=acp)]
    assert recovery_sets(db, "chat-1", "acp-1", {"agent-1"}) == ({"agent-1"}, set())


@pytest.fixture
def recovered_chat(tmp_path, monkeypatch):
    rows = [receipt()]
    service = Mock()
    service.db.list_studio_chat_messages_tail.side_effect = lambda *a, **kw: list(rows)

    def append(session_id, kind, role, content):
        rows.append({"seq": len(rows) + 1, "kind": kind, "content": content.copy()})

    service.store.append_message.side_effect = append
    monkeypatch.setattr(wake, "task_root", lambda *_: tmp_path)
    # Execute a bounded watcher synchronously: no test threads or sleeps.
    monkeypatch.setattr(
        wake.threading, "Thread", lambda *, target, **kw: SimpleNamespace(start=target)
    )

    def run():
        runtime = SessionRuntime(SimpleNamespace(cwd=str(tmp_path), send_prompt=Mock()), "token")
        runtime.kimi_agent = True
        runtime.background_stop = Mock()
        runtime.background_stop.wait.side_effect = [False, False, True]
        service.runtime.return_value = runtime
        wake.start_watcher(service, "chat-1", runtime, "acp-1")
        runtime.handle.send_prompt.assert_not_called()

    write_task(tmp_path, status="completed", description="Finish pending work")
    return service, rows, run, append


def test_running_before_disconnect_gets_one_terminal_receipt_on_resume(recovered_chat):
    service, rows, run, _ = recovered_chat
    run()
    assert rows[-1]["content"]["event"] == "background_task_finished"
    assert rows[-1]["content"]["acp_session_id"] == "acp-1"
    run()
    assert service.store.append_message.call_count == 1


def test_failed_terminal_append_remains_recoverable_after_another_resume(recovered_chat):
    service, rows, run, append = recovered_chat
    service.store.append_message.side_effect = RuntimeError("database unavailable")
    run()
    assert len(rows) == 1
    service.store.append_message.side_effect = append
    run()
    assert len(rows) == 2
    run()
    assert len(rows) == 2


def test_failed_history_read_retries_without_losing_recovery(recovered_chat):
    service, rows, run, _ = recovered_chat
    service.db.list_studio_chat_messages_tail.side_effect = [RuntimeError("offline"), rows.copy()]
    run()
    assert rows[-1]["content"]["event"] == "background_task_finished"


def test_one_receipt_failure_does_not_block_other_tasks_or_replay_success(tmp_path):
    from server.app.studio_chat.background_receipts import ReceiptCursor

    cursor = ReceiptCursor(tmp_path, "acp-1", set())
    service = Mock()
    service.db.list_studio_chat_messages_tail.return_value = []
    write_task(tmp_path, "broken", status="completed")
    write_task(tmp_path, "healthy", status="completed")
    recorded = []

    def append(session_id, kind, role, content):
        if content["task_id"] == "broken":
            raise RuntimeError("one receipt rejected")
        recorded.append(content["task_id"])

    service.store.append_message.side_effect = append
    assert cursor.step(service, "chat-1") == {"healthy"}
    assert recorded == ["healthy"]
    assert cursor.step(service, "chat-1") == set()
    service.store.append_message.side_effect = lambda *args: recorded.append(args[3]["task_id"])
    assert cursor.step(service, "chat-1") == {"broken"}
    assert recorded == ["healthy", "broken"]
