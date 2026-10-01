"""Kimi V1 completion files wake an idle chat without a human prompt (#806)."""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat import background_wakeup as wake
from server.app.studio_chat.kimi_task_store import completed_tasks, task_root
from server.app.studio_chat.runtime import SessionRuntime
from tests.helpers import wait_for_predicate

pytestmark = pytest.mark.no_db


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
        assert target != path, "special file must be rejected before open"
        return original(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    assert completed_tasks(tmp_path, "acp-1") == {}


def test_reader_rechecks_file_replaced_between_stat_and_open(tmp_path, monkeypatch):
    path = write_task(tmp_path, status="completed") / "runtime.json"
    original = os.open
    descriptors = []

    def replace_with_fifo(target, flags, *args, **kwargs):
        if target == path:
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
    service.db.claim_studio_chat_turn.return_value = True
    monkeypatch.setattr(wake, "keepalive_run_token", Mock())
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
    wait_for_predicate(lambda: service.store.append_message.call_count == 2)
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
    monkeypatch.setattr(
        wake, "keepalive_run_token", lambda *_: setattr(runtime, "token_keepalive_done", True)
    )
    assert not wake.wake_session(service, "chat-1", runtime, ["agent-1"])
    service.db.claim_studio_chat_turn.assert_not_called()


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
