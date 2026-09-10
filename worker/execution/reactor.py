"""Reactor-based stdout pump for agent executions (#578 phase 1).

The legacy model is thread-per-execution: every velites child owns one Python
pump thread reading its stdout pipe (``worker.event_filter``). At fleet scale
(hundreds of concurrent agents) the sheer thread count — kernel wakeups, GIL
contention, context switches — taxes the executor far more than the agents
themselves (issue #578: executor 352% CPU supervising a fleet using 111%);
#566's heartbeat starvation was a direct casualty of that model.

This module replaces N pump threads with:

1. **one reactor thread** multiplexing every registered pipe via
   ``selectors`` — the kernel returns a batch of ready fds per wakeup, and
   the (blocking) ``select()`` call releases the GIL, so the reactor is not
   fighting the executor for it;
2. **a small parse pool** (core-count-scale, not fleet-scale) doing the
   per-line JSON parse + delta filtering (``worker.event_filter`` rules),
   then the writes into each execution's events file.

The reactor only owns the byte→line→event segment: spawn, lease/heartbeat,
timeout policing, and the #564 ownership semantics stay where they are
(``worker.execution.run`` / ``process_lifecycle``). Per-stream ordering is
preserved: each stream's lines are appended to its pending queue under the
stream lock and written by pool tasks in queue order, so an execution's
events.jsonl is byte-identical to the legacy pump.

Backpressure is explicit and by design: when an events file cannot keep up,
the per-stream backlog bound pauses the fd and the child's 64KB pipe fills
and blocks the *child* — the agent slows down instead of events being
dropped. Do not "fix" that (#578 design point 3).
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from worker.event_filter import _DELTA_PREFIX, DROP_EVENT_TYPES, spawn_event_pump

# One read syscall's worth per ready fd; velites JSON lines fit comfortably
# (message_end with the full final message is the largest and stays well
# under this in a single read; longer lines reassemble across reads).
_READ_SIZE = 65536
# A single unterminated line beyond this is unsalvageable noise; keep the
# tail bounded instead of buffering forever (legacy readline had the same
# practical failure mode via MemoryError).
_MAX_LINE_BYTES = 64 * 1024 * 1024
# Per-stream backlog of framed lines awaiting pool writes. Generous beyond
# any legit per-turn burst; hitting it means the events file is wedged and
# pipe backpressure should engage rather than memory growing unbounded.
_STREAM_BACKLOG = 2048


# Pool size: core-count-scale (issue #578 "N = 核数级 8–16"), bounded for
# the common machine shapes; json.loads releases the GIL well.
def _default_parse_workers() -> int:
    return max(2, min(16, os.cpu_count() or 4))


def _filter_line(raw: bytes) -> bool:
    """Denylist decision for one framed line; False = drop (delta spam).

    Byte-identical semantics to ``worker.event_filter.pump_filtered_events``:
    fast prefix path first, then parse-and-check; unknown and non-JSON
    content always passes through."""
    if raw.startswith(_DELTA_PREFIX):
        return False
    try:
        event = json.loads(raw)
    except ValueError:
        return True  # stderr text, crash traces, partial lines
    return not (isinstance(event, dict) and event.get("type") in DROP_EVENT_TYPES)


class _Stream:
    """One registered child stdout: fd bookkeeping, partial-line buffer,
    and the bounded pending queue feeding the parse pool.

    Pool protocol (all under ``lock``): ``inflight`` counts submitted pool
    tasks, ``writer`` is the single-writer token (False→True flip claims it;
    the holder drains the queue in a loop — per-stream line order is the
    whole point), ``finishing`` marks the fd unregistered. ``done`` fires
    exactly when finishing AND pending is empty AND inflight is 0 — the
    leaving task checks that conjunction, which makes ``join`` race-free
    against a task holding the last batch mid-write."""

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
        "inflight",
        "writer",
        "parse_error",
    )

    def __init__(self, proc: subprocess.Popen[bytes], path: str) -> None:
        if proc.stdout is None:  # spawn contract: PIPE is always set here
            raise ReactorUnavailable("child has no stdout pipe")
        self.fd = proc.stdout.fileno()
        self.proc = proc
        self.buf = bytearray()
        self.path = path
        self.pending: list[bytes] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.paused = False
        self.finishing = False
        self.inflight = 0
        self.writer = False
        self.parse_error: BaseException | None = None

    def take_pending(self) -> list[bytes]:
        with self.lock:
            batch = self.pending
            self.pending = []
            return batch


class EventPumpReactor:
    """Process-wide singleton multiplexing agent stdout pipes (#578).

    ``get()`` lazily starts the reactor thread + parse pool; ``register``
    hooks one spawned child's stdout into it and returns a handle whose
    ``join()`` mirrors the legacy pump thread's semantics (waits for the
    stream's buffered lines to be written out). Fail-closed: a reactor-
    internal error unregisters every stream — each execution falls back to
    the legacy per-thread pump — and disables the reactor for the process
    lifetime.
    """

    _singleton: EventPumpReactor | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, parse_workers: int | None = None) -> None:
        self._parse_workers = (
            _default_parse_workers() if parse_workers is None else max(1, parse_workers)
        )
        self._pool = ThreadPoolExecutor(self._parse_workers, thread_name_prefix="event-parse")
        self._selector = selectors.DefaultSelector()
        self._streams: dict[int, _Stream] = {}
        self._wakeup_r, self._wakeup_w = os.pipe()
        os.set_blocking(self._wakeup_r, False)
        self._selector.register(self._wakeup_r, selectors.EVENT_READ, data=None)
        self._stop = threading.Event()
        self._failed = False
        self._streams_lock = threading.Lock()
        self._thread = threading.Thread(target=self._react, name="event-pump-reactor", daemon=True)
        self._thread.start()

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def get(cls) -> EventPumpReactor:
        with cls._singleton_lock:
            if cls._singleton is None or cls._singleton.is_dead():
                cls._singleton = EventPumpReactor()
            return cls._singleton

    def is_dead(self) -> bool:
        return self._failed

    def _register_stream(self, stream: _Stream) -> None:
        """Register one stream's fd or raise if the reactor is disabled."""
        with self._streams_lock:
            if self._failed:
                raise ReactorUnavailable("reactor disabled after internal error")
            self._streams[stream.fd] = stream
        self._selector.register(stream.fd, selectors.EVENT_READ, data=stream)
        self._wake()

    def shutdown(self) -> None:
        """Stop the reactor; pool shutdown waits for queued writes."""
        with EventPumpReactor._singleton_lock:
            if EventPumpReactor._singleton is self:
                EventPumpReactor._singleton = None
        self._stop.set()
        self._wake()
        self._thread.join(timeout=10)
        self._pool.shutdown(wait=True)
        with contextlib.suppress(Exception):
            self._selector.close()
        with contextlib.suppress(OSError):
            os.close(self._wakeup_r)
        with contextlib.suppress(OSError):
            os.close(self._wakeup_w)

    def _wake(self) -> None:
        with contextlib.suppress(OSError, BlockingIOError):
            os.write(self._wakeup_w, b"x")

    # -- registration ----------------------------------------------------

    def register(self, proc: subprocess.Popen[bytes], output_path: str) -> ReactorPumpHandle:
        stream = _Stream(proc, output_path)
        self._register_stream(stream)
        return ReactorPumpHandle(self, stream)

    def unregister(self, stream: _Stream) -> None:
        """Stop reading a stream (child exited or fatal error) and requeue its
        remaining bytes for a final drain; ``done`` fires once the queue is
        empty and no pool task holds a batch (see _Stream docstring)."""
        with self._streams_lock:
            self._streams.pop(stream.fd, None)
        with contextlib.suppress(KeyError, ValueError):
            self._selector.unregister(stream.fd)
        with stream.lock:
            if stream.buf:
                stream.pending.append(bytes(stream.buf))
                stream.buf.clear()
            stream.finishing = True
            already_drained = not stream.pending and stream.inflight == 0
            needs_task = bool(stream.pending) and stream.inflight == 0
        if already_drained:
            stream.done.set()
        elif needs_task:
            # Nobody is in flight to observe completion; submit the final
            # drain task (it sets done through the same conjunction check).
            self._submit_parse(stream)

    # -- reactor core ----------------------------------------------------

    def _react(self) -> None:
        try:
            while not self._stop.is_set():
                events = self._selector.select(timeout=0.5)
                for key, _mask in events:
                    if key.data is None:
                        with contextlib.suppress(OSError, BlockingIOError):
                            os.read(self._wakeup_r, _READ_SIZE)
                        continue
                    self._read_ready(key.data)
        except Exception as exc:
            # #204 broad-except audit: reactor 存活语义——这是监督模型的心脏，
            # 任何逃逸（selector/os 的 OSError、编程错误）都不允许带崩
            # executor 进程。吞是对的：fail-closed 降级——注销全部流（各执行
            # 由调用方回落 legacy thread pump 跑完），_failed 让本进程后续
            # spawn 不再走 reactor。日志保全：print 异常与降级后果。
            print(
                f"event-pump reactor failed, falling back to thread pumps: {exc!r}",
                flush=True,
            )
            self._fail_closed(exc)
        finally:
            with contextlib.suppress(Exception):
                self._selector.close()

    def _read_ready(self, stream: _Stream) -> None:
        # Drain until EAGAIN/short read so one wakeup empties the child's
        # writes; non-blocking fds make os.read raise instead of blocking.
        while not stream.paused:
            chunk = os.read(stream.fd, _READ_SIZE)
            if not chunk:
                self.unregister(stream)  # EOF: child exited
                return
            stream.buf += chunk
            while True:
                nl = stream.buf.find(b"\n")
                if nl < 0:
                    break
                line, _, stream.buf = stream.buf.partition(b"\n")
                stripped = bytes(line.strip())
                if stripped:
                    self._enqueue(stream, stripped)
            if len(stream.buf) > _MAX_LINE_BYTES:
                stream.buf = stream.buf[-_READ_SIZE:]
            if len(chunk) < _READ_SIZE:
                return  # short read ≈ pipe drained for now

    def _enqueue(self, stream: _Stream, line: bytes) -> None:
        with stream.lock:
            stream.pending.append(line)
            deep = len(stream.pending) >= _STREAM_BACKLOG
            if deep and not stream.paused:
                stream.paused = True
        if deep:
            # Backpressure by design (#578 point 3): stop reading this fd;
            # the child's pipe fills and the child blocks — events slow down
            # instead of being dropped or buffered unbounded.
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(stream.fd)
        self._submit_parse(stream)

    def _submit_parse(self, stream: _Stream) -> None:
        with stream.lock:
            stream.inflight += 1
        self._pool.submit(self._parse_one, stream)

    def _resume(self, stream: _Stream) -> None:
        """Re-arm a paused fd (pool calls when the backlog has drained)."""
        with self._streams_lock:
            if self._failed or stream.fd not in self._streams:
                return
            with stream.lock:
                stream.paused = False
            try:
                self._selector.register(stream.fd, selectors.EVENT_READ, data=stream)
            except (KeyError, ValueError):
                return  # raced with unregister; the stream is finishing
        self._wake()

    def _parse_one(self, stream: _Stream) -> None:
        """Pool task: drain the stream's pending queue, in order, exclusively.

        Ordering contract: the ``writer`` flag is the per-stream write token —
        only the task that flips it False→True writes; every other task
        returns immediately and relies on the token holder to loop until the
        queue is empty (single-writer per stream ⇒ appends hit the file in
        queue order). The token is released in ``finally`` so a holder's
        crash cannot wedge the stream forever."""
        with stream.lock:
            if stream.writer:
                # Someone already owns the write token for this stream; that
                # task will drain whatever we enqueued before returning.
                stream.inflight -= 1
                return
            stream.writer = True
        try:
            while True:
                batch = stream.take_pending()
                if not batch:
                    break
                kept = [line for line in batch if _filter_line(line)]
                with open(stream.path, "ab") as output:
                    for line in kept:
                        output.write(line)
                        output.write(b"\n")
                with stream.lock:
                    backlog = len(stream.pending)
                    was_paused = stream.paused
                if was_paused and backlog < _STREAM_BACKLOG // 2:
                    self._resume(stream)
        except Exception as exc:
            # #204 broad-except audit: parse-pool 生存语义。单流写失败（磁盘
            # 满、OSError）不得带崩池线程或 reactor。吞是对的：记入
            # stream.parse_error（观测面）并注销该流——事件面已不可信，
            # 该执行照常走退出/超时上报，损失的是 events.jsonl 完整性
            # （stderr trace 可判读）。日志保全：print。
            stream.parse_error = exc
            print(f"event-pump parse failed for {stream.path}: {exc!r}", flush=True)
            self.unregister(stream)
        finally:
            with stream.lock:
                stream.writer = False
            self._retire_if_idle(stream)

    def _retire_if_idle(self, stream: _Stream) -> None:
        """Completion check from the leaving task: finishing + empty queue +
        no other task in flight ⇒ every line is on disk, fire ``done``.

        The writer-token holder re-checks the queue after releasing the
        token: a task that skipped (token busy) may have enqueued the final
        lines and returned, leaving the holder responsible for them."""
        with stream.lock:
            stream.inflight -= 1
            finished = stream.finishing and not stream.pending and stream.inflight == 0
            requeued = stream.finishing and bool(stream.pending) and stream.inflight == 0
        if finished:
            stream.done.set()
        elif requeued:
            # Final lines arrived after the last task skipped; run one more
            # drain pass for them.
            self._submit_parse(stream)

    def _drop(self, stream: _Stream) -> None:
        """Force-drop a wedged stream (join timed out): no final drain — the
        pool is the wedge point, queueing more work would only pile up."""
        with self._streams_lock:
            self._streams.pop(stream.fd, None)
        with contextlib.suppress(KeyError, ValueError):
            self._selector.unregister(stream.fd)
        stream.done.set()

    def _fail_closed(self, exc: BaseException) -> None:
        with self._streams_lock:
            self._failed = True
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.parse_error = exc
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(stream.fd)
            stream.done.set()  # unblock joiners; failure already printed
        self._pool.shutdown(wait=False)


class ReactorUnavailable(RuntimeError):
    """Reactor is disabled (post-failure); caller must use the legacy pump."""


class ReactorPumpHandle:
    """Per-execution facade mirroring the legacy pump thread's join()."""

    def __init__(self, reactor: EventPumpReactor, stream: _Stream) -> None:
        self._reactor = reactor
        self._stream = stream

    def join(self, timeout: float | None = None) -> None:
        """Wait for the stream to be fully written to disk.

        Mirrors ``threading.Thread.join`` semantics: returns silently on
        timeout (never raises — a legacy pump thread dying never failed the
        execution; parse failures are recorded on the stream and printed by
        the pool). On timeout the stream is force-dropped so a wedged pool
        cannot leak it into the selector forever."""
        if self._stream.done.wait(timeout=timeout):
            return
        self._reactor._drop(self._stream)


def spawn_agent_pump(proc: subprocess.Popen[bytes], output: Any, execution_id: str) -> Any:
    """Event pump facade for one spawned agent process (#578 phase 1).

    Default: the process-wide reactor (one selector thread + a core-count
    parse pool replaces one pump thread per execution). Opt-out
    ``AGENT_WORKER_EVENT_PUMP=thread`` keeps the legacy per-execution thread;
    any reactor-internal failure also falls back to it (fail-closed), so the
    pump surface — ``join(timeout)`` — is identical either way."""
    if os.environ.get("AGENT_WORKER_EVENT_PUMP", "reactor") == "reactor":
        try:
            return EventPumpReactor.get().register(proc, output.name)
        except ReactorUnavailable:
            print(
                f"event-pump reactor unavailable for {execution_id}; using thread pump",
                flush=True,
            )
    return spawn_event_pump(proc, output, f"pi-events-{execution_id[:8]}")
