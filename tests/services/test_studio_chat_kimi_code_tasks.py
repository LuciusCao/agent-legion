"""Background-task receipts read the Kimi Code storage layout (#972).

Kimi CLI V1 keeps ``<share>/sessions/<md5>/<sid>/tasks/<id>/{spec,runtime}.json``
(test_studio_chat_background_wakeup.py); Kimi Code keeps
``<home>/sessions/<wd>/<sid>/agents/main/tasks/<id>.json``. On a Kimi Code
session the watcher posts #772 receipts but never a #816 wakeup prompt: the
engine opens its own turn on a task notification (#938).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.app.studio_chat import background_delivery as delivery
from server.app.studio_chat import background_wakeup as wake
from server.app.studio_chat.background_baseline import capture_resume_baseline
from server.app.studio_chat.kimi_code_tasks import kimi_code_task_root
from server.app.studio_chat.kimi_task_store import completed_tasks, task_snapshots
from server.app.studio_chat.runtime import SessionRuntime
from tests.helpers import wait_for_predicate

pytestmark = pytest.mark.no_db

SID = "session_972"


def _session_dir(home, sid=SID):
    path = home / "sessions" / "wd_ws_972" / sid
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_code_task(home, task_id="agent-x1abcdef", status="running", **fields):
    tasks = _session_dir(home) / "agents" / "main" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    info = {
        "taskId": task_id,
        "kind": "agent",
        "description": "Survey the docs",
        "status": status,
        "detached": True,
        "startedAt": 1_700_000_000_000,
        "endedAt": None,
        **fields,
    }
    (tasks / f"{task_id}.json").write_text(json.dumps(info))
    return tasks


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "kimi-code-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path / "v1-share"))
    return home


def test_task_root_locates_kimi_code_session(home, tmp_path):
    assert kimi_code_task_root(str(tmp_path), SID) is None
    session = _session_dir(home)
    assert kimi_code_task_root(str(tmp_path), SID) == session / "agents" / "main" / "tasks"
    assert kimi_code_task_root(str(tmp_path), "../escape") is None


def test_snapshots_map_kimi_code_task_info(home, tmp_path):
    root = write_code_task(
        home,
        "agent-done0001",
        "completed",
        endedAt=1_700_000_042_000,
        stopReason="handoff",
    )
    write_code_task(home, "bash-run00001", "running", kind="process", description="")
    write_code_task(home, "bash-fore0001", "completed", kind="process", detached=False)
    write_code_task(home, "question-q0000001", "completed", kind="question")
    write_code_task(home, "agent-other001", "completed", taskId="agent-mismatch")
    (root / "bash-run00001").mkdir()
    (root / "bash-run00001" / "output.log").write_text("line\n")
    tasks = task_snapshots(root, SID)
    assert set(tasks) == {"agent-done0001", "bash-run00001"}
    done, running = tasks["agent-done0001"], tasks["bash-run00001"]
    assert (done.kind, done.status, done.terminal) == ("agent", "completed", True)
    assert (done.started_at, done.finished_at) == (1_700_000_000.0, 1_700_000_042.0)
    assert done.summary == "handoff"
    assert (running.kind, running.terminal, running.description) == ("bash", False, "bash-run00001")
    assert running.output_changed_at is not None
    assert completed_tasks(root, SID) == {"agent-done0001": "completed"}


def test_subagent_journal_is_the_progress_signal(home):
    root = write_code_task(home, "agent-busy0001", agentId="agent-0")
    assert task_snapshots(root, SID)["agent-busy0001"].output_changed_at is None
    journal = root.parent.parent / "agent-0" / "wire.jsonl"
    journal.parent.mkdir()
    journal.write_text("{}\n")
    progress = task_snapshots(root, SID)["agent-busy0001"].output_changed_at
    assert progress == pytest.approx(journal.stat().st_mtime)


def test_missing_task_directory_is_empty_even_strict(home):
    root = _session_dir(home) / "agents" / "main" / "tasks"
    assert completed_tasks(root, SID, strict=True) == {}


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_linked_task_info_is_rejected(home, tmp_path, link):
    root = write_code_task(home, "agent-link0001", "completed")
    foreign = tmp_path / "foreign.json"
    (root / "agent-link0001.json").rename(foreign)
    target = root / "agent-link0001.json"
    os.symlink(foreign, target) if link == "symlink" else os.link(foreign, target)
    assert task_snapshots(root, SID) == {}
    with pytest.raises(ValueError):
        task_snapshots(root, SID, strict=True)


def test_resume_baseline_reads_kimi_code_layout(home, tmp_path):
    root = write_code_task(home, "agent-hist0001", "completed")
    baseline = capture_resume_baseline(str(tmp_path), SID)
    assert baseline is not None
    assert (baseline.root, baseline.finished) == (root, frozenset({"agent-hist0001"}))


@pytest.fixture
def chat(home, tmp_path, monkeypatch):
    existing_threads = set(threading.enumerate())
    runtime = SessionRuntime(
        SimpleNamespace(cwd=str(tmp_path), send_prompt=Mock(return_value=True)), "token"
    )
    runtime.kimi_agent = True
    service = Mock()
    service.runtime.return_value = runtime
    service.db.claim_studio_chat_turn.return_value = True
    service.db.list_studio_chat_messages_tail.return_value = []
    monkeypatch.setattr(delivery, "_token_alive", Mock(return_value=True))
    monkeypatch.setattr(wake, "POLL_SECONDS", 0.01)
    yield service, runtime
    runtime.background_stop.set()
    for thread in set(threading.enumerate()) - existing_threads:
        if thread.name == "studio-kimi-completions":
            thread.join(timeout=5)


def _events(service):
    return [call.args[3] for call in service.store.append_message.call_args_list]


def test_kimi_code_session_gets_receipts_but_no_wakeup(chat, home, caplog):
    service, runtime = chat
    _session_dir(home)
    caplog.set_level(logging.WARNING)
    wake.start_watcher(service, "chat-1", runtime, SID)
    assert runtime.background_cursor.wakes is False
    write_code_task(home, "agent-work0001", "running")
    wait_for_predicate(lambda: len(_events(service)) == 1)
    write_code_task(home, "agent-work0001", "completed", endedAt=1_700_000_005_000)
    wait_for_predicate(lambda: len(_events(service)) == 2)
    assert _events(service)[-1]["event"] == "background_task_finished"
    assert _events(service)[-1]["kind"] == "agent"
    runtime.handle.send_prompt.assert_not_called()
    service.db.claim_studio_chat_turn.assert_not_called()
    assert "Kimi completion watcher failed" not in caplog.text


def test_watcher_adopts_kimi_code_layout_once_session_appears(chat, home, caplog):
    service, runtime = chat
    caplog.set_level(logging.WARNING)
    wake.start_watcher(service, "chat-1", runtime, SID)  # no Kimi Code session yet: V1
    assert runtime.background_cursor.wakes is True
    # The session directory appears atomically with a finished task in it.
    staging = home.parent / "staging"
    write_code_task(staging, "agent-late0001", "completed")
    home.mkdir()
    os.rename(staging / "sessions", home / "sessions")
    wait_for_predicate(lambda: runtime.background_cursor.wakes is False)
    caplog.clear()
    write_code_task(home, "bash-late00001", "running", kind="process")
    wait_for_predicate(lambda: any(e["task_id"] == "bash-late00001" for e in _events(service)))
    assert all(event["task_id"] != "agent-late0001" for event in _events(service))
    runtime.handle.send_prompt.assert_not_called()
    assert "Kimi completion watcher failed" not in caplog.text


@pytest.mark.parametrize(
    "document", ["{}", '{"taskId": "agent-part0001"}', '{"status": "completed"}']
)
def test_incomplete_task_info_fails_strict_baseline_only(home, document):
    root = write_code_task(home, "agent-good0001", "completed")
    (root / "agent-part0001.json").write_text(document)
    assert set(task_snapshots(root, SID)) == {"agent-good0001"}
    with pytest.raises(ValueError):
        task_snapshots(root, SID, strict=True)


def test_strict_snapshot_rejects_task_root_replaced_during_scan(home, monkeypatch):
    from server.app.studio_chat import kimi_code_tasks

    root = write_code_task(home, "agent-done0001", "completed")
    original = kimi_code_tasks.read_code_task

    def replace_root(*args, **kwargs):
        task = original(*args, **kwargs)
        if root.exists():
            root.rename(root.parent / "tasks-old")
            write_code_task(home, "agent-new00001", "running")
        return task

    monkeypatch.setattr(kimi_code_tasks, "read_code_task", replace_root)
    with pytest.raises(OSError):
        task_snapshots(root, SID, strict=True)
