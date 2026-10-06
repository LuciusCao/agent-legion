"""Kimi Code wire journal: shared open policy and bounded retry backlog (#1044).

The journal is opened through the SECURITY-PATH-002 primitive (single-link
regular files only, never blocking), and a backlog left by failed timeline
writes is persisted before the journal is read any further.
"""

from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace

import pytest

from server.app.fs_safety import NotRegularFileError
from server.app.studio_chat.kimi_wire import WireTail
from server.app.studio_chat.unprompted_turns import UnpromptedTurnWatcher
from server.app.studio_chat.wire_baseline import capture_wire_baseline

pytestmark = pytest.mark.no_db


def _turn(turn_id: int, text: str) -> list[dict]:
    return [
        {
            "type": "turn.prompt",
            "agentId": "main",
            "origin": {"kind": "cron_job"},
            "turnId": turn_id,
        },
        {
            "type": "context.append_loop_event",
            "agentId": "main",
            "event": {
                "type": "content.part",
                "turnId": str(turn_id),
                "part": {"type": "text", "text": text},
            },
        },
        {"type": "turn.ended", "agentId": "main", "turnId": turn_id, "reason": "completed"},
    ]


def _journal(root, session="session_1044"):
    path = root / "home" / "sessions" / "wd_ws" / session / "agents" / "main" / "wire.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path


def _append(path, records):
    with path.open("a") as handle:
        handle.writelines(json.dumps(record) + "\n" for record in records)


def test_wire_tail_refuses_multiply_linked_journal_without_reading(tmp_path, monkeypatch):
    other = _journal(tmp_path, "session_other")
    _append(other, _turn(1, "OTHER-SESSION-CONTENT"))
    path = _journal(tmp_path)
    path.unlink()
    os.link(other, path)
    monkeypatch.setattr(os, "pread", lambda *_: pytest.fail("journal content was read"))
    with pytest.raises(NotRegularFileError):
        WireTail(path).read()
    with pytest.raises(NotRegularFileError):
        WireTail(path).baseline()


def test_wire_baseline_defers_on_multiply_linked_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    other = _journal(tmp_path, "session_other")
    path = _journal(tmp_path)
    path.unlink()
    os.link(other, path)
    baseline = capture_wire_baseline(str(tmp_path), "session_1044")
    assert baseline is not None and baseline.identity is None


def test_wire_tail_refuses_fifo_journal_without_blocking(tmp_path):
    path = _journal(tmp_path)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(NotRegularFileError):
        WireTail(path).read()


class _FlakyStore:
    def __init__(self) -> None:
        self.failing = True
        self.rows: list[tuple[str, dict]] = []

    def append_message(self, _session_id, kind, _role, content):
        if self.failing:
            raise RuntimeError("database unavailable")
        self.rows.append((kind, content))

    def mark_mcp_verified(self, _session_id) -> None:
        pass

    def publish_session(self, _session_id) -> None:
        pass


def test_backlog_does_not_grow_while_writes_fail_and_lands_in_order(tmp_path):
    path = _journal(tmp_path)
    store = _FlakyStore()
    runtime = SimpleNamespace(lock=threading.Lock(), closed=False, mcp_observed=False)
    service = SimpleNamespace(store=store, runtime=lambda _sid: runtime)
    watcher = UnpromptedTurnWatcher(service, "chat-1", runtime, lambda: path)
    _append(path, _turn(1, "FIRST"))
    with pytest.raises(RuntimeError):
        watcher.step()
    backlog, offset = len(watcher.pending), watcher.tail.offset
    assert backlog == 3
    _append(path, _turn(2, "SECOND"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            watcher.step()
        # The journal is paused: no further read, no growth.
        assert (len(watcher.pending), watcher.tail.offset) == (backlog, offset)
    store.failing = False
    watcher.step()  # backlog first ...
    watcher.step()  # ... then the journal resumes
    texts = [content.get("text") for kind, content in store.rows if kind == "text"]
    assert texts == ["FIRST", "SECOND"]
    assert watcher.pending == []
    assert [content.get("event") for _kind, content in store.rows].count("turn_end") == 2
