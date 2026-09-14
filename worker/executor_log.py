"""Persistent rolling logs for the worker panel lines and structured events.

The supervisor's panel log (executor stdout + supervisor lifecycle lines)
used to live only in a 500-line in-memory deque: a crash or a busy episode
scrolled the evidence away (the #566 investigation pain point). Every panel
line is now ALSO appended to a size-rotated file — 10 MiB × 5 keeps weeks
of normal operation and bounds the worst case at ~60 MiB.

#510 splits the structured events out: ``emit_event``'s single-line JSON
objects (claim.attempt / execution.* / http.error) go to their own
``events.jsonl`` sink beside the panel log, so jq-style timeline tooling
consumes pure JSON without filtering the panel's rhythm text (slots lines,
restart notices). The split happens in the supervisor's ``_log`` shim — the
executor itself keeps one stdout channel (#204 discipline: the observer
never breaks the observed loop).

Path rule: the logs sit next to the worker's data domain — ``data/logs/
executor-<state dir 名>.log`` (and ``events-<state dir 名>.jsonl``) when the
state dir lives under a ``data/`` root (the default
``data/agent-worker-service`` layout; the name suffix keeps two co-located
state dirs from sharing one file, PR #572), else ``<state_dir>/logs/
executor.log`` so a custom state dir never sprays logs into an unexpected
parent.

Rotation is hand-rolled (not RotatingFileHandler): logging's emit swallows
write errors into handleError, which would break the report-once contract
below. Writes never break the panel: the first OSError mutes the sink.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

# One structured event line per claim round / execution / upload task — a
# far slower rhythm than the panel text, so the events file gets a tighter
# cap: 5 MiB × 3 bounds the worst case at ~20 MiB while keeping the
# low-frequency exception events (http.error, claim.backoff) reachable for
# a full busy episode (#510's acceptance: 30 minutes of full load).
EVENTS_MAX_BYTES = 5 * 1024 * 1024
EVENTS_BACKUP_COUNT = 3


def executor_log_path(state_dir: Path) -> Path:
    """The rolling executor log's path for this worker's state dir.

    The filename carries the state dir name (PR #572 review): the two worker
    state dirs (``data/agent-worker`` and ``data/agent-worker-service``)
    share one ``data/logs/`` parent on a combined host, and two sinks on one
    file would stomp each other's rotation."""
    if state_dir.parent.name == "data":
        return state_dir.parent / "logs" / f"executor-{state_dir.name}.log"
    return state_dir / "logs" / "executor.log"


def events_log_path(state_dir: Path) -> Path:
    """The structured-events sink's path (#510): same anchoring rule as the
    panel log, ``.jsonl`` extension, ``events-`` prefix."""
    if state_dir.parent.name == "data":
        return state_dir.parent / "logs" / f"events-{state_dir.name}.jsonl"
    return state_dir / "logs" / "events.jsonl"


class ExecutorLogSink:
    """Append panel lines to the rolling file; lazily opened, never fatal."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES, backups: int = BACKUP_COUNT):
        self._path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._handle: Any = None
        self._lock = threading.Lock()
        self._broken = False
        self._closed = False

    def write(self, line: str, on_error: Callable[[str], None]) -> None:
        """Append one line; the first failure reports once via ``on_error``
        and the sink mutes itself. After ``close()`` writes are dropped (a
        relay thread outliving its join must not reopen the file, PR #572)."""
        with self._lock:
            # 锁内检查：过检后阻塞在锁上的线程，在 close() 持锁置位后拿到锁，
            # 不得走下面的懒开分支把文件重开（PR #572 评审）。
            if self._broken or self._closed:
                return
            try:
                if self._handle is None:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._handle = self._path.open("a", encoding="utf-8")
                if self._handle.tell() + len(line) >= self._max_bytes:
                    self._rotate()
                self._handle.write(line + "\n")
                self._handle.flush()
            except (OSError, ValueError) as exc:
                # ValueError: 句柄被外部关闭（I/O operation on closed file）。
                self._broken = True
                on_error(f"executor 滚动日志写入失败（{self._path}）：{exc}；后续仅保留内存日志")

    def _rotate(self) -> None:
        """caller holds the lock: shift .1→.2…, current→.1, reopen fresh."""
        self._handle.close()
        for index in range(self._backups - 1, 0, -1):
            older = self._path.with_name(f"{self._path.name}.{index}")
            newer = self._path.with_name(f"{self._path.name}.{index + 1}")
            if older.exists():
                if index + 1 > self._backups:
                    older.unlink()
                else:
                    os.replace(older, newer)
        if self._path.exists():
            os.replace(self._path, self._path.with_name(f"{self._path.name}.1"))
        self._handle = self._path.open("a", encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def resume(self) -> None:
        """Re-arm after close (supervisor start() following a stop())."""
        self._closed = False


def is_structured_event(message: str) -> bool:
    """#510: does this executor stdout line carry a #490 structured event?

    The events module emits ``{"event": ..., "ts": ...}`` as single-line JSON
    (sorted keys — ``event`` leads). The sniff is deliberately cheap and
    conservative: a false negative strands one line in the panel file (the
    pre-#510 behavior), a false positive puts one text line in events.jsonl;
    neither changes any runtime behavior. A full json.loads would buy exact
    classification at a per-line cost the panel loop does not need."""
    return message.startswith('{"event":') and message.endswith("}")


class PanelLogSinks:
    """The supervisor's two rolling sinks: the panel log (every line,
    timestamped by the caller) and the structured-events file (#510).

    Bundled so the supervisor's ``_log`` stays one delegation: panel lines
    always hit the panel sink; a recognized event line ALSO lands untimestamped
    in events.jsonl (the JSON body carries its own ``ts``). Both sinks share
    ExecutorLogSink's failure semantics — the first OSError mutes that sink,
    never the panel loop. Lifecycle (resume/close) pairs with the
    supervisor's start()/stop()."""

    def __init__(self, state_dir: Path) -> None:
        self.panel = ExecutorLogSink(executor_log_path(state_dir))
        self.events = ExecutorLogSink(
            events_log_path(state_dir), max_bytes=EVENTS_MAX_BYTES, backups=EVENTS_BACKUP_COUNT
        )

    def write(self, message: str, line: str, on_error: Callable[[str], None]) -> None:
        """``line`` is the timestamped panel form of ``message``."""
        self.panel.write(line, on_error)
        if is_structured_event(message):
            self.events.write(message, on_error)

    def resume(self) -> None:
        self.panel.resume()
        self.events.resume()

    def close(self) -> None:
        self.panel.close()
        self.events.close()
