"""Tests for the #578 phase-1 event pump reactor (worker/execution/reactor.py).

The reactor replaces one pump thread per agent execution with a single
selector thread + a core-count parse pool. These cases pin:

- parity with the legacy pump's filter semantics (delta spam dropped,
  unknown and non-JSON lines kept, order preserved per stream);
- lifecycle: register → child exits → join() returns after the file has
  every line;
- the ``AGENT_WORKER_EVENT_PUMP=thread`` opt-out keeps the legacy path;
- fail-closed: a dead reactor makes register() raise ReactorUnavailable and
  run_execution falls back to the thread pump.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from worker.event_filter import spawn_event_pump
from worker.execution.reactor import EventPumpReactor, ReactorUnavailable


def _spawn_writer(lines: list[str]) -> subprocess.Popen[bytes]:
    """A child that writes the given lines to stdout then exits."""
    script = "import sys, time\n"
    for line in lines:
        script += f"sys.stdout.write({line!r} + '\\n')\n"
        script += "sys.stdout.flush()\n"
    script += "time.sleep(0.05)\n"
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _read_events(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]


def _fresh_reactor() -> EventPumpReactor:
    EventPumpReactor._singleton = None  # test isolation: never reuse across cases
    return EventPumpReactor.get()


def test_reactor_writes_kept_lines_in_order(tmp_path: Path) -> None:
    lines = [
        '{"type":"agent_start"}',
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}',
        '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta"}}',
        '{"type":"message_end","message":{"role":"assistant"}}',
        "Traceback (most recent call last):",
        '{"type":"some_future_event"}',
    ]
    out = tmp_path / "events.jsonl"
    out.touch()
    reactor = _fresh_reactor()
    try:
        proc = _spawn_writer(lines)
        handle = reactor.register(proc, str(out))
        proc.wait(timeout=10)
        handle.join(timeout=10)
    finally:
        proc.kill()
        reactor.shutdown()
    assert _read_events(out) == [
        '{"type":"agent_start"}',
        '{"type":"message_end","message":{"role":"assistant"}}',
        "Traceback (most recent call last):",
        '{"type":"some_future_event"}',
    ]


def test_reactor_multiplexes_many_children(tmp_path: Path) -> None:
    """N children sharing one reactor each get their own complete file."""
    count = 8
    out_paths = [tmp_path / f"events-{i}.jsonl" for i in range(count)]
    for p in out_paths:
        p.touch()
    reactor = _fresh_reactor()
    try:
        handles = []
        for i, out in enumerate(out_paths):
            proc = _spawn_writer(
                [
                    json.dumps({"type": "agent_start", "n": i}),
                    json.dumps({"type": "agent_end", "n": i}),
                ]
            )
            handles.append((proc, reactor.register(proc, str(out))))
        for proc, _handle in handles:
            proc.wait(timeout=10)
        for _proc, handle in handles:
            handle.join(timeout=10)
    finally:
        for proc, _ in handles:
            proc.kill()
        reactor.shutdown()
    for i, out in enumerate(out_paths):
        events = [json.loads(ln) for ln in _read_events(out)]
        assert [e["type"] for e in events] == ["agent_start", "agent_end"]
        assert all(e["n"] == i for e in events)


def test_reactor_preserves_line_order_under_load(tmp_path: Path) -> None:
    """A fast child (200 lines in one burst) forces the pending queue through
    multiple pool batches; the single-writer token must keep the file in
    exact FIFO order with nothing dropped (regression: two concurrent pool
    tasks used to write their batches out of order)."""
    total = 200
    out = tmp_path / "events.jsonl"
    out.touch()
    reactor = _fresh_reactor()
    try:
        proc = _spawn_writer([json.dumps({"type": "keep", "seq": i}) for i in range(total)])
        handle = reactor.register(proc, str(out))
        proc.wait(timeout=10)
        handle.join(timeout=10)
    finally:
        proc.kill()
        reactor.shutdown()
    seqs = [json.loads(ln)["seq"] for ln in _read_events(out)]
    assert seqs == list(range(total))


def test_reactor_join_timeout_returns_silently(tmp_path: Path) -> None:
    """A child that keeps its pipe open: join(timeout) returns, no raise."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    out = tmp_path / "events.jsonl"
    out.touch()
    reactor = _fresh_reactor()
    try:
        handle = reactor.register(proc, str(out))
        started = time.monotonic()
        handle.join(timeout=0.2)
        assert time.monotonic() - started < 5
    finally:
        proc.kill()
        proc.wait(timeout=5)
        reactor.shutdown()


def test_reactor_partial_line_flushed_on_exit(tmp_path: Path) -> None:
    """A child killed mid-line: whatever was buffered still lands in the file
    (legacy pumps wrote the last readline's content verbatim)."""
    out = tmp_path / "events.jsonl"
    out.touch()
    reactor = _fresh_reactor()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'import os, time\nos.write(1, b\'{\\"type\\":\\"agent_start\\"}\')\ntime.sleep(30)\n',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        handle = reactor.register(proc, str(out))
        time.sleep(0.3)
        proc.kill()
        proc.wait(timeout=5)
        handle.join(timeout=10)
    finally:
        proc.kill()
        reactor.shutdown()
    assert _read_events(out) == ['{"type":"agent_start"}']


def test_dead_reactor_refuses_registration(tmp_path: Path) -> None:
    reactor = _fresh_reactor()
    reactor._fail_closed(RuntimeError("boom"))
    try:
        proc = _spawn_writer(['{"type":"agent_start"}'])
        try:
            reactor.register(proc, str(tmp_path / "events.jsonl"))
            raise AssertionError("register on dead reactor must raise")
        except ReactorUnavailable:
            pass
        finally:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        reactor.shutdown()
    # The dead singleton must not be handed out again: get() transparently
    # replaces it with a fresh, healthy reactor (fail-closed self-heal for
    # subsequent executions).
    EventPumpReactor._singleton = reactor
    try:
        replacement = EventPumpReactor.get()
        assert replacement is not reactor
        assert not replacement.is_dead()
        proc = _spawn_writer(['{"type":"agent_start"}'])
        try:
            out = tmp_path / "healed.jsonl"
            out.touch()
            handle = replacement.register(proc, str(out))
            proc.wait(timeout=10)
            handle.join(timeout=10)
            assert _read_events(out) == ['{"type":"agent_start"}']
        finally:
            proc.kill()
            proc.wait(timeout=5)
        replacement.shutdown()
    finally:
        EventPumpReactor._singleton = None


def test_thread_pump_opt_out_still_works(tmp_path: Path) -> None:
    """The legacy per-execution pump path (AGENT_WORKER_EVENT_PUMP=thread) is
    unchanged: the facade in run.py must be able to reach it."""
    proc = _spawn_writer(['{"type":"agent_start"}', '{"type":"message_update","x":1}'])
    out = tmp_path / "events.jsonl"
    try:
        with out.open("wb") as output:
            pump = spawn_event_pump(proc, output, "test-pump")
            proc.wait(timeout=10)
            pump.join(timeout=10)
    finally:
        proc.kill()
    assert _read_events(out) == ['{"type":"agent_start"}']
