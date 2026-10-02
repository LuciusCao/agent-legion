"""Kimi V1 completion files wake an idle chat without a human prompt (#806)."""

import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat import background_delivery as delivery
from server.app.studio_chat import background_wakeup as wake
from server.app.studio_chat.kimi_task_store import completed_tasks, task_root
from server.app.studio_chat.runtime import SessionRuntime
from tests.helpers import wait_for_predicate

pytestmark = pytest.mark.no_db


def test_running_and_bash_completion_are_visible_without_waking_model(chat, tmp_path):
    service, runtime = chat
    write_task(tmp_path, "shell-1", "running", kind="bash", description="Check documents")
    wake.start_watcher(service, "chat-1", runtime, "acp-1")
    wait_for_predicate(lambda: service.store.append_message.call_count == 1)
    assert service.store.append_message.call_args.args[3]["status"] == "running"
    runtime.handle.send_prompt.assert_not_called()
    write_task(tmp_path, "shell-1", "completed", kind="bash", description="Check documents")
    wait_for_predicate(lambda: service.store.append_message.call_count == 2)
    assert service.store.append_message.call_args.args[3]["event"] == "background_task_finished"
    runtime.handle.send_prompt.assert_not_called()


def test_cancel_does_not_hide_task_receipts(chat, tmp_path):
    service, runtime = chat
    runtime.background_wakeup_enabled = False
    wake.start_watcher(service, "chat-1", runtime, "acp-1")
    write_task(tmp_path, "agent-1", "failed")
    wait_for_predicate(lambda: service.store.append_message.call_count == 1)
    assert service.store.append_message.call_args.args[3]["status"] == "failed"
    runtime.handle.send_prompt.assert_not_called()


def write_task(root, task_id="agent-1", status="running", **spec_overrides):
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    spec = {
        "version": 1,
        "id": task_id,
        "session_id": "acp-1",
        "kind": "agent",
        "owner_role": "root",
        **spec_overrides,
    }
    (path / "spec.json").write_text(json.dumps(spec))
    (path / "runtime.json").write_text(json.dumps({"status": status}))
    return path


def test_reader_filters_foreign_nested_unknown_and_nonterminal_tasks(tmp_path):
    write_task(tmp_path, "agent-1", "completed")
    write_task(tmp_path, "agent-2", "failed", session_id="foreign")
    write_task(tmp_path, "agent-3", "failed", owner_role="subagent")
    write_task(tmp_path, "agent-4", "failed", version=2)
    write_task(tmp_path, "agent-5", "running")
    write_task(tmp_path, "agent-6", "completed", kind="bash")
    assert completed_tasks(tmp_path, "acp-1") == {"agent-1": "completed"}
    assert completed_tasks(tmp_path, "acp-1", ignored={"agent-1"}) == {}


def test_reader_rejects_symlinks_partial_and_oversized_files(tmp_path):
    root = tmp_path / "tasks"
    path = write_task(root)
    runtime_file = path / "runtime.json"
    runtime_file.write_text("{")
    assert completed_tasks(root, "acp-1") == {}
    runtime_file.write_text(" " * 65536 + '{"status":"completed"}')
    assert completed_tasks(root, "acp-1") == {}
    target = tmp_path / "foreign.json"
    target.write_text('{"status":"completed"}')
    runtime_file.unlink()
    runtime_file.symlink_to(target)
    assert completed_tasks(root, "acp-1") == {}


def test_task_root_matches_kimi_layout_and_rejects_traversal(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))
    cwd = tmp_path / "workspace"
    digest = hashlib.md5(str(cwd).encode(), usedforsecurity=False).hexdigest()
    assert task_root(str(cwd), "acp-1") == tmp_path / "sessions" / digest / "acp-1" / "tasks"
    assert task_root(str(cwd), "../foreign") is None


@pytest.mark.parametrize("filename", ["spec.json", "runtime.json"])
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_reader_rejects_special_files_before_open(tmp_path, monkeypatch, filename, kind):
    path = write_task(tmp_path, status="completed") / filename
    path.unlink()
    os.mkfifo(path) if kind == "fifo" else path.mkdir()
    original = os.open

    def guarded_open(target, *args, **kwargs):
        assert target != filename, "special file must be rejected before open"
        return original(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    assert completed_tasks(tmp_path, "acp-1") == {}


def test_reader_rechecks_file_replaced_between_stat_and_open(tmp_path, monkeypatch):
    path = write_task(tmp_path, status="completed") / "runtime.json"
    original = os.open
    descriptors = []

    def replace_with_fifo(target, flags, *args, **kwargs):
        if target == "runtime.json":
            path.unlink()
            os.mkfifo(path)
            assert flags & os.O_NONBLOCK
        descriptor = original(target, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", replace_with_fifo)
    assert completed_tasks(tmp_path, "acp-1") == {}
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    runtime = SessionRuntime(
        SimpleNamespace(cwd=str(tmp_path), send_prompt=Mock(return_value=True)), "token"
    )
    runtime.kimi_agent = True
    service = Mock()
    service.runtime.return_value = runtime
    service._runtimes_lock = threading.Lock()
    service._runtimes = {"chat-1": runtime}
    service.db.claim_studio_chat_turn.return_value = True
    service.db.list_studio_chat_messages_tail.return_value = []
    monkeypatch.setattr(delivery, "invalidate_run_token", Mock())
    monkeypatch.setattr(delivery, "_token_alive", Mock(return_value=True))
    monkeypatch.setattr(wake, "task_root", lambda *_: tmp_path)
    monkeypatch.setattr(wake, "POLL_SECONDS", 0.01)
    yield service, runtime
    runtime.background_stop.set()


def test_completion_wakes_once_without_user_prompt_and_records_receipt(chat, tmp_path):
    service, runtime = chat
    write_task(tmp_path)
    wake.start_watcher(service, "chat-1", runtime, "acp-1")
    write_task(tmp_path, status="completed")
    wait_for_predicate(lambda: runtime.handle.send_prompt.call_count == 1)
    assert "agent-1" in runtime.handle.send_prompt.call_args.args[0]
    assert runtime.turn_open
    receipt = service.store.append_message.call_args.args[3]
    assert receipt["event"] == "background_task_finished"
    assert receipt["status"] == "completed"
    # A new completion proves the watcher took another lap, without replaying
    # the first receipt; the ongoing assistant turn defers the next wake.
    write_task(tmp_path, "agent-2", "failed")
    wait_for_predicate(
        lambda: (
            sum(
                c.args[3]["event"] == "background_task_finished"
                for c in service.store.append_message.call_args_list
            )
            == 2
        )
    )
    assert runtime.handle.send_prompt.call_count == 1
    runtime.turn_open = False
    wait_for_predicate(lambda: runtime.handle.send_prompt.call_count == 2)


@pytest.mark.parametrize(
    "guard", ["closed", "compacting", "turn_open", "cancelled", "stale", "busy"]
)
def test_wakeup_respects_runtime_and_turn_ownership(chat, guard):
    service, runtime = chat
    if guard == "cancelled":
        runtime.background_wakeup_enabled = False
    elif guard == "stale":
        service.runtime.return_value = SessionRuntime(runtime.handle, "new-token")
    elif guard == "busy":
        service.db.claim_studio_chat_turn.return_value = False
    else:
        setattr(runtime, guard, True)
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    runtime.handle.send_prompt.assert_not_called()


def test_dead_token_does_not_start_a_followup(chat, monkeypatch):
    service, runtime = chat
    monkeypatch.setattr(delivery, "_token_alive", lambda *_: False)
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    service.db.claim_studio_chat_turn.assert_not_called()


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("fault", ["write", "snapshot", "notice"])
def test_automatic_dead_token_stops_despite_projection_failure(chat, monkeypatch, queued, fault):
    from server.app.studio_chat.token_keepalive import invalidate_run_token

    service, runtime = chat
    runtime.handle.request_stop = Mock()
    monkeypatch.setattr(delivery, "invalidate_run_token", invalidate_run_token)
    monkeypatch.setattr(
        delivery, "_token_alive", Mock(side_effect=[True, False] if queued else [False])
    )
    service.db.get_scoped_token_user.side_effect = AssertionError("must not re-query known death")
    target = {
        "write": service.db.update_studio_chat_session_if,
        "snapshot": service.store.publish_session,
        "notice": service.store.append_message,
    }[fault]
    target.side_effect = RuntimeError("projection failed")
    if queued:
        assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
        assert not runtime.handle.send_prompt.call_args.kwargs["before_start"]()
    else:
        assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
        service.db.claim_studio_chat_turn.assert_not_called()
    runtime.handle.request_stop.assert_called_once()
    service.db.get_scoped_token_user.assert_not_called()


def test_dead_handle_exposes_recovery_instead_of_a_stuck_running_turn(chat):
    service, runtime = chat
    runtime.handle.send_prompt.return_value = False
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    assert not runtime.turn_open
    service.db.update_studio_chat_session_if.assert_called_once_with(
        "chat-1", status_in=("running",), status="error"
    )


def test_snapshot_failure_does_not_retry_an_already_queued_prompt(chat):
    service, runtime = chat
    service.store.publish_session.side_effect = RuntimeError("temporary read failure")
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    runtime.handle.send_prompt.assert_called_once()


def test_teardown_between_claim_and_delivery_wins(chat):
    service, runtime = chat
    service.runtime.side_effect = [runtime, None]
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    assert not runtime.turn_open
    runtime.handle.send_prompt.assert_not_called()


def test_resume_does_not_replay_old_terminal_tasks(chat, tmp_path):
    service, runtime = chat
    write_task(tmp_path, "agent-old", "completed")
    wake.start_watcher(service, "chat-1", runtime, "acp-1")
    write_task(tmp_path, "agent-new", "completed")
    wait_for_predicate(lambda: runtime.handle.send_prompt.call_count == 1)
    prompt = runtime.handle.send_prompt.call_args.args[0]
    assert "agent-new" in prompt
    assert "agent-old" not in prompt
    assert service.store.append_message.call_count == 1


def test_cancelled_watcher_discards_completions_until_human_rearms(chat, tmp_path):
    service, runtime = chat
    runtime.background_wakeup_enabled = False
    wake.start_watcher(service, "chat-1", runtime, "acp-1")
    write_task(tmp_path, "agent-cancelled", "completed")
    # Wait on the reader rather than timing the worker thread.
    original = wake.completed_tasks
    with pytest.MonkeyPatch.context() as patch:
        observed = Mock(wraps=original)
        patch.setattr(wake, "completed_tasks", observed)
        wait_for_predicate(lambda: observed.call_count >= 2)
    runtime.background_wakeup_enabled = True
    write_task(tmp_path, "agent-new", "completed")
    wait_for_predicate(lambda: runtime.handle.send_prompt.call_count == 1)
    assert "agent-cancelled" not in runtime.handle.send_prompt.call_args.args[0]
    assert any(
        c.args[3]["task_id"] == "agent-cancelled"
        and c.args[3]["event"] == "background_task_finished"
        for c in service.store.append_message.call_args_list
    )


def test_cancel_rearm_without_watcher_lap_discards_old_pending_and_completions(chat, tmp_path):
    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    runtime.turn_open = True
    write_task(tmp_path, "pending", "completed")
    cursor.step(service, "chat-1", runtime)
    assert cursor.pending == {"pending"}
    wake.cancel_wakeup(runtime)
    write_task(tmp_path, "cancelled", "completed")
    wake.rearm_wakeup(runtime)
    runtime.turn_open = False
    write_task(tmp_path, "new", "completed")
    cursor.step(service, "chat-1", runtime)
    prompt = runtime.handle.send_prompt.call_args.args[0]
    assert "new" in prompt and "pending" not in prompt and "cancelled" not in prompt
    assert {
        c.args[3]["task_id"]
        for c in service.store.append_message.call_args_list
        if c.args[3]["event"] == "background_task_finished"
    } == {"pending", "cancelled", "new"}


def test_rearm_preparation_does_not_enable_failed_send_or_swallow_new_tasks(chat, tmp_path):
    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    wake.cancel_wakeup(runtime)
    write_task(tmp_path, "cancelled", "completed")
    commit = wake.prepare_rearm(runtime)
    assert not runtime.background_wakeup_enabled
    write_task(tmp_path, "new", "completed")
    commit()
    cursor.step(service, "chat-1", runtime)
    assert "cancelled" not in runtime.handle.send_prompt.call_args.args[0]
    assert "new" in runtime.handle.send_prompt.call_args.args[0]


def test_cleanup_failure_blocks_delivery_but_not_activity_receipts(chat, tmp_path):
    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    runtime.background_cleanup = Mock(return_value=False)
    write_task(tmp_path, "new", "running")
    cursor.step(service, "chat-1", runtime)
    assert service.store.append_message.call_args.args[3]["status"] == "running"
    write_task(tmp_path, "new", "completed")
    cursor.step(service, "chat-1", runtime)
    assert service.store.append_message.call_args.args[3]["event"] == "background_task_finished"
    assert cursor.pending == {"new"}
    runtime.handle.send_prompt.assert_not_called()
    runtime.background_cleanup.return_value = True
    cursor.step(service, "chat-1", runtime)
    runtime.handle.send_prompt.assert_called_once()
    assert service.store.append_message.call_count == 2


def test_rearm_commit_cannot_undo_a_newer_cancel(chat):
    _, runtime = chat
    wake.cancel_wakeup(runtime)
    commit = wake.prepare_rearm(runtime)
    wake.cancel_wakeup(runtime)
    commit()
    assert not runtime.background_wakeup_enabled


@pytest.mark.parametrize("failure", ["token", "enqueue"])
def test_admission_failures_never_leave_claim_or_lose_pending(chat, tmp_path, monkeypatch, failure):
    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    write_task(tmp_path, status="completed")
    if failure == "token":
        delivery._token_alive.side_effect = RuntimeError("database unavailable")
    else:
        runtime.handle.send_prompt.side_effect = RuntimeError("queue unavailable")
    with pytest.raises(RuntimeError):
        cursor.step(service, "chat-1", runtime)
    assert not runtime.turn_open
    assert cursor.pending == {"agent-1"}
    if failure == "token":
        service.db.claim_studio_chat_turn.assert_not_called()
    else:
        service.db.update_studio_chat_session_if.assert_called_once_with(
            "chat-1", status_in=("running",), status="idle"
        )


@pytest.mark.parametrize("cause", ["cancel", "compacting", "token_error", "token_dead", "replaced"])
def test_queued_followup_rechecks_cancellation_credentials_and_identity(chat, tmp_path, cause):
    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    guard = runtime.handle.send_prompt.call_args.kwargs["before_start"]
    if cause == "cancel":
        wake.cancel_wakeup(runtime)
        wake.rearm_wakeup(runtime)
    elif cause == "compacting":
        runtime.compacting = True
    elif cause == "token_error":
        delivery._token_alive.side_effect = RuntimeError("database unavailable")
    elif cause == "token_dead":
        delivery._token_alive.return_value = False
    else:
        replacement = SessionRuntime(runtime.handle, "replacement")
        service.runtime.return_value = replacement
        service._runtimes["chat-1"] = replacement
    assert not guard()
    if cause == "replaced":
        service.db.update_studio_chat_session_if.assert_not_called()
    else:
        assert not runtime.turn_open
        assert cursor.pending == (set() if cause == "cancel" else {"agent-1"})


def test_acp_queue_cancel_before_consumption_never_calls_agent(chat):
    import asyncio
    from unittest.mock import AsyncMock

    from server.app.studio_chat.acp_session import _CLOSE, AcpSessionHandle

    service, runtime = chat
    handle = AcpSessionHandle(
        command="kimi", args=[], cwd="/tmp", mcp_server=Mock(), env=None, callbacks=Mock()
    )
    runtime.handle = handle
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    wake.cancel_wakeup(runtime)
    wake.rearm_wakeup(runtime)
    handle._queue.put(_CLOSE)
    conn = SimpleNamespace(prompt=AsyncMock())
    asyncio.run(handle._prompt_loop(conn, "acp-1"))
    conn.prompt.assert_not_awaited()
    handle.callbacks.on_turn_end.assert_not_called()
    assert not runtime.turn_open


def test_stale_queued_followup_cannot_release_new_human_turn(chat):
    from server.app.studio_chat.turn_state import open_turn

    service, runtime = chat
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    guard = runtime.handle.send_prompt.call_args.kwargs["before_start"]
    wake.cancel_wakeup(runtime)
    # Prior on_turn_end settles the old claim, then a human claims a new turn.
    open_turn(runtime, "new human prompt")
    owner = runtime.turn_owner
    wake.rearm_wakeup(runtime)
    assert not guard()
    assert runtime.turn_open and runtime.turn_owner is owner
    service.db.update_studio_chat_session_if.assert_not_called()


@pytest.mark.parametrize("new_human_turn", [False, True])
def test_failed_queue_cleanup_retries_only_its_owned_claim(chat, tmp_path, new_human_turn):
    from server.app.studio_chat.turn_state import open_turn

    service, runtime = chat
    cursor = runtime.background_cursor = wake.CompletionCursor(tmp_path, "acp-1")
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    guard = runtime.handle.send_prompt.call_args.kwargs["before_start"]
    wake.cancel_wakeup(runtime)
    service.db.update_studio_chat_session_if.side_effect = RuntimeError(
        "temporary database failure"
    )
    assert not guard()  # Must not leak into the unowned generic on_turn_error callback.
    assert runtime.background_cleanup is not None
    service.db.update_studio_chat_session_if.side_effect = None
    service.db.update_studio_chat_session_if.reset_mock()
    if new_human_turn:
        open_turn(runtime, "new human prompt")
    cursor.step(service, "chat-1", runtime)
    assert runtime.background_cleanup is None
    assert runtime.turn_open == new_human_turn
    assert service.db.update_studio_chat_session_if.call_count == (0 if new_human_turn else 1)


def test_stop_requested_handle_rejects_automatic_prompt(chat):
    from server.app.studio_chat.acp_session import AcpSessionHandle

    service, runtime = chat
    handle = AcpSessionHandle(
        command="kimi", args=[], cwd="/tmp", mcp_server=Mock(), env=None, callbacks=Mock()
    )
    runtime.handle = handle
    handle.request_stop()
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    assert not runtime.turn_open
    assert handle._queue.qsize() == 1  # Only the stop sentinel; no unreachable prompt.


def test_close_without_captured_runtime_does_not_remove_concurrent_resume():
    from server.app.studio_chat.service import StudioChatService

    service = object.__new__(StudioChatService)
    service._runtimes_lock = threading.Lock()
    service._runtimes = {}
    service.get_session = Mock(return_value={"status": "idle"})
    service.store = Mock()
    replacement = SessionRuntime(Mock(), "replacement")

    def resume_after_close_write(*_args, **_kwargs):
        service._runtimes["chat-1"] = replacement

    service._db = Mock()
    service._db.update_studio_chat_session.side_effect = resume_after_close_write
    service.close_session("chat-1", "workspace")
    assert service.runtime("chat-1") is replacement
    assert not replacement.closed
    replacement.handle.close.assert_not_called()


def test_close_notification_failure_still_tears_down_owned_runtime():
    from server.app.studio_chat.service import StudioChatService

    service = object.__new__(StudioChatService)
    runtime = SessionRuntime(Mock(), "token")
    service._runtimes_lock = threading.Lock()
    service._runtimes = {"chat-1": runtime}
    service._db = Mock()
    service.get_session = Mock(return_value={"status": "idle"})
    service.store = Mock()
    service.store.append_message.side_effect = RuntimeError("notification persistence failure")
    service.close_session("chat-1", "workspace")
    assert runtime.closed
    assert service.runtime("chat-1") is None
    runtime.handle.close.assert_called_once()


@pytest.mark.parametrize("cancel_before_task_start", [False, True])
def test_dispatch_guard_runs_in_prompt_task_after_dequeue(chat, cancel_before_task_start):
    import asyncio
    from unittest.mock import AsyncMock

    from server.app.studio_chat.prompt_turn import run_prompt_turn

    service, runtime = chat
    assert wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    guard = runtime.handle.send_prompt.call_args.kwargs["before_start"]
    conn = SimpleNamespace(prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")))

    async def dispatch():
        if cancel_before_task_start:
            asyncio.get_running_loop().call_soon(wake.cancel_wakeup, runtime)
        return await run_prompt_turn(
            conn, "acp-1", "completion", on_timeout=Mock(), before_start=guard
        )

    result = asyncio.run(dispatch())
    assert conn.prompt.await_count == (0 if cancel_before_task_start else 1)
    assert (result.response is None) == cancel_before_task_start


def test_reader_pins_ancestor_descriptor_during_replacement(tmp_path, monkeypatch):
    root = tmp_path / "tasks"
    write_task(root, status="running")
    foreign = tmp_path / "foreign"
    write_task(foreign, status="completed")
    original = os.open
    swapped = False

    def swap_ancestor(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "agent-1" and not swapped:
            swapped = True
            root.rename(tmp_path / "original")
            root.symlink_to(foreign, target_is_directory=True)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_ancestor)
    assert completed_tasks(root, "acp-1") == {}
    assert swapped


def test_reader_rejects_task_directory_swapped_to_foreign_symlink(tmp_path, monkeypatch):
    root = tmp_path / "tasks"
    write_task(root, status="running")
    foreign = tmp_path / "foreign"
    task = write_task(foreign, status="completed")
    original = os.open

    def swap_task(name, flags, *args, **kwargs):
        if name == "agent-1":
            (root / name).rename(root / "old")
            (root / name).symlink_to(task, target_is_directory=True)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_task)
    assert completed_tasks(root, "acp-1") == {}
