"""Unavailable metadata is distinct from a successfully observed empty history."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat.background_baseline import capture_resume_baseline
from server.app.studio_chat.background_wakeup import CompletionCursor, cancel_wakeup, rearm_wakeup
from server.app.studio_chat.kimi_task_store import completed_tasks, task_root
from server.app.studio_chat.runtime import SessionRuntime
from tests.helpers.studio_chat_fixtures import write_task

pytestmark = pytest.mark.no_db


def test_unavailable_resume_history_defers_instead_of_freezing_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path))
    assert capture_resume_baseline(str(tmp_path), "acp-1") is None
    root = task_root(str(tmp_path), "acp-1")
    root.mkdir(parents=True)
    baseline = capture_resume_baseline(str(tmp_path), "acp-1")
    assert baseline.finished == frozenset()
    write_task(root, status="completed")
    (root / "agent-1" / "runtime.json").unlink()
    assert capture_resume_baseline(str(tmp_path), "acp-1") is None


def test_unavailable_initial_baseline_retries_without_replaying_history(tmp_path):
    root = tmp_path / "tasks"
    cursor = CompletionCursor(root, "acp-1")
    assert not cursor.initialized
    runtime = SessionRuntime(SimpleNamespace(), "token")
    runtime.background_cursor = cursor
    runtime.turn_open = True
    service = Mock()
    service.db.list_studio_chat_messages_tail.return_value = []
    service.runtime.return_value = runtime
    service._runtimes_lock = threading.Lock()
    service._runtimes = {"chat": runtime}
    with pytest.raises(OSError):
        cursor.step(service, "chat", runtime)
    write_task(root, "historical", "completed")
    cursor.step(service, "chat", runtime)
    assert cursor.initialized and cursor.seen == {"historical"}
    service.store.append_message.assert_not_called()
    write_task(root, "new", "completed")
    cursor.step(service, "chat", runtime)
    assert cursor.pending == {"new"}


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_strict_baseline_rejects_root_replacement(tmp_path, monkeypatch, replacement):
    root = tmp_path / "tasks"
    write_task(root, status="completed")
    original = os.listdir

    def replace(fd):
        names = original(fd)
        root.rename(tmp_path / "old")
        if replacement == "directory":
            root.mkdir()
        else:
            root.symlink_to(tmp_path / "old", target_is_directory=True)
        return names

    monkeypatch.setattr(os, "listdir", replace)
    with pytest.raises(OSError):
        completed_tasks(root, "acp-1", strict=True)


@pytest.mark.parametrize("payload", ["{", "[]", "{}", " " * 65537])
def test_failed_baseline_does_not_partially_advance_cursor(tmp_path, payload):
    cursor = CompletionCursor(tmp_path, "acp-1", seen=frozenset({"previous"}))
    cursor.pending.add("previous")
    write_task(tmp_path, "valid", "completed")
    task = write_task(tmp_path, "unreadable", "completed")
    (task / "runtime.json").write_text(payload)
    with pytest.raises(ValueError):
        cursor.baseline()
    assert cursor.seen == {"previous"}
    assert cursor.pending == {"previous"}
    write_task(tmp_path, "unreadable", "completed")
    cursor.baseline()
    assert cursor.seen == {"previous", "valid", "unreadable"}
    assert not cursor.pending


@pytest.mark.parametrize("filename", ["spec.json", "runtime.json"])
@pytest.mark.parametrize("kind", ["missing", "fifo", "symlink", "incomplete"])
def test_strict_baseline_rejects_unobserved_task_metadata(tmp_path, filename, kind):
    path = write_task(tmp_path, status="completed") / filename
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "incomplete":
        path.write_text("{}")
    with pytest.raises((OSError, ValueError)):
        completed_tasks(tmp_path, "acp-1", strict=True)


def test_strict_baseline_excludes_foreign_tasks_before_reading_runtime(tmp_path):
    path = write_task(tmp_path, session_id="foreign")
    (path / "runtime.json").unlink()
    assert completed_tasks(tmp_path, "acp-1", strict=True) == {}


@pytest.mark.parametrize("rearm_first", [False, True])
def test_delayed_initial_baseline_does_not_replay_agent_or_bash_receipts(tmp_path, rearm_first):
    root = tmp_path / "tasks"
    cursor = CompletionCursor(root, "acp-1")
    runtime = SessionRuntime(SimpleNamespace(), "token")
    runtime.background_cursor = cursor
    runtime.turn_open = True
    service = Mock()
    service.runtime.return_value = runtime
    service.db.list_studio_chat_messages_tail.return_value = []
    write_task(root, "historical-agent", "completed")
    write_task(root, "historical-bash", "completed", kind="bash")
    if rearm_first:
        cancel_wakeup(runtime)
        rearm_wakeup(runtime)
    cursor.step(service, "chat", runtime)
    service.store.append_message.assert_not_called()
    assert not cursor.pending
    write_task(root, "new-bash", "running", kind="bash")
    cursor.step(service, "chat", runtime)
    assert service.store.append_message.call_args.args[3]["task_id"] == "new-bash"
    assert not cursor.pending
