"""Stream bookkeeping for the #578 event-pump reactor.

``_Stream`` is one registered child stdout: fd, partial-line buffer, the
bounded pending queue, and the pool protocol under ``lock``. The helpers here
are the pieces the reactor core (``reactor.py``) calls from multiple threads —
they live apart only for the file-size budget, not for any layering reason.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading

from worker.event_filter import spawn_event_pump


class ReactorUnavailable(RuntimeError):
    """Reactor is disabled (post-failure); caller must use the legacy pump."""


class _Stream:
    """One registered child stdout: fd bookkeeping, partial-line buffer, the
    bounded pending queue, and the pool protocol under ``lock`` — ``inflight``
    counts submitted tasks, ``writer`` is the single-writer token (the holder
    drains the queue in a loop: per-stream line order), ``finishing`` marks
    the fd unregistered, ``dropped`` fences late writers. ``done`` fires when
    finishing AND pending empty AND inflight 0 — checked by the leaving task,
    making ``join`` race-free against a mid-write batch."""

    __slots__ = (
        "fd",
        "proc",
        "buf",
        "path",
        "pending",
        "lock",
        "done",
        "paused",
        "finishing",
        "dropped",
        "inflight",
        "writer",
        "parse_error",
    )

    def __init__(self, proc: subprocess.Popen[bytes], path: str) -> None:
        if proc.stdout is None:  # spawn contract: PIPE is always set here
            raise ReactorUnavailable("child has no stdout pipe")
        self.fd = proc.stdout.fileno()
        # The drain loop reads until EAGAIN — the fd must be non-blocking or a
        # full-pipe read followed by an empty pipe would park the whole
        # reactor thread on the second os.read (review P0: pipe capacity
        # equals _READ_SIZE on Linux, making that a certainty, not a tail).
        os.set_blocking(self.fd, False)
        self.proc = proc
        self.buf = bytearray()
        self.path = path
        self.pending: list[bytes] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.paused = False
        self.finishing = False
        self.dropped = False
        self.inflight = 0
        self.writer = False
        self.parse_error: BaseException | None = None

    def take_pending(self) -> list[bytes]:
        with self.lock:
            batch = self.pending
            self.pending = []
            return batch


def stream_dropped(stream: _Stream) -> bool:
    """Lock-guarded read of the drop fence (cross-thread mutation is the
    point; a bare attribute read is fine for a bool but keeps mypy honest)."""
    with stream.lock:
        return stream.dropped


def takeover_with_legacy_pump(stream: _Stream) -> None:
    """Fail-closed takeover (codex review): hand the child's stdout to a
    legacy per-thread pump — these executions already hold a handle and never
    re-enter spawn_agent_pump, so without this their children wedge on a full
    pipe until the execution timeout. Restore blocking mode first (the legacy
    pump iterates blocking); append-mode re-open keeps continuity with what
    the pool already wrote. The handle deliberately outlives this scope: the
    pump thread owns it."""
    with contextlib.suppress(OSError):
        os.set_blocking(stream.fd, True)
    try:
        # The fallback pump thread owns this handle (not this scope).
        fallback_output = open(stream.path, "ab")  # noqa: SIM115
    except OSError as open_error:
        print(f"event-pump fallback open failed for {stream.path}: {open_error!r}", flush=True)
        return
    if stream.proc.stdout is not None:
        spawn_event_pump(stream.proc, fallback_output, f"pi-events-fallback-{stream.fd}")
