"""Reactor-based stdout pump for agent executions (#578 phase 1).

Legacy: thread-per-execution — at fleet scale the pump-thread count (kernel
wakeups, GIL contention, context switches) taxes the executor more than the
agents themselves (#578: 352% CPU supervising a fleet using 111%; #566's
heartbeat starvation was a casualty). This module replaces N pump threads
with one selector thread (batched ready-fds per wakeup; the blocking select
releases the GIL) plus a core-count parse pool overlapping file writes with
reads across streams (json.loads itself stays GIL-bound).

Scope: the reactor only owns the byte→line→event segment — spawn,
lease/heartbeat, timeout policing, and the #564 ownership semantics stay in
``run.py`` / ``process_lifecycle``. Per-stream ordering is preserved via the
pending queue + single-writer token (lines are written whitespace-normalized
with one trailing ``\\n`` — semantically identical to the legacy raw writes).

Backpressure is by design (#578 point 3): when an events file cannot keep
up, the backlog bound pauses the fd, the child's 64KB pipe fills, and the
*child* blocks — events slow down instead of being dropped. Do not "fix".

Fail-closed scope: a reactor-internal error unregisters every stream and
disables the reactor — subsequent spawns fall back to the legacy per-thread
pump. Streams already registered at failure time are handed to a legacy pump
too (their buffered-but-unwritten lines are lost; the fallback continues
reading the child's stdout from that point on).
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
from worker.execution.reactor_stream import (
    ReactorUnavailable,
    _Stream,
    stream_dropped,
    takeover_with_legacy_pump,
)

# One read syscall's worth per ready fd; velites JSON lines fit comfortably
# (message_end with the full final message is the largest and stays well
# under this in a single read; longer lines reassemble across reads).
_READ_SIZE = 65536
# A single unterminated line beyond this is unsalvageable noise; the head is
# kept (the JSON "type" key lives there) and the tail discarded, bounding
# memory instead of buffering forever.
_MAX_LINE_BYTES = 64 * 1024 * 1024
# Per-stream backlog of framed lines awaiting pool writes. Generous beyond
# any legit per-turn burst; hitting it means the events file is wedged and
# pipe backpressure should engage rather than memory growing unbounded.
_STREAM_BACKLOG = 2048


# Pool size: core-count-scale (issue #578 "N = 核数级 8–16"), bounded for
# the common machine shapes; the pool overlaps file writes across streams
# (json.loads itself stays GIL-bound — verified, it is NOT the parallel part).
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


class EventPumpReactor:
    """Process-wide singleton: ``get()`` lazily starts the reactor thread +
    parse pool; ``register`` returns a handle whose ``join()`` mirrors the
    legacy pump thread's semantics. Fail-closed scope: see module docstring."""

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
        try:
            self._selector.register(stream.fd, selectors.EVENT_READ, data=stream)
        except Exception:
            # #204 broad-except audit: register 失败（selector 内部 OSError）
            # 时回滚 dict 条目防泄漏——流从未被监听，调用方经
            # spawn_agent_pump 的宽捕获把该执行报 failed（等价于旧泵起不来
            # 的结局）。吞是对的：让调用方拿到原始异常上下文前先恢复
            # reactor 自身一致性。日志保全：异常向上传播。
            with self._streams_lock:
                self._streams.pop(stream.fd, None)
            raise
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
            # #204 broad-except audit: reactor 存活语义——任何逃逸都不允许
            # 带崩 executor 进程。吞是对的：fail-closed 降级（注销全部流 +
            # 后续 spawn 回落 legacy pump，在途流语义见模块 docstring）。
            # 日志保全：print 异常与降级后果。
            print(
                f"event-pump reactor failed, falling back to thread pumps: {exc!r}",
                flush=True,
            )
            self._fail_closed(exc)
        finally:
            with contextlib.suppress(Exception):
                self._selector.close()

    def _read_ready(self, stream: _Stream) -> None:
        # Drain until EAGAIN (the authoritative drained signal on a
        # non-blocking fd) so one wakeup empties the child's writes without
        # ever parking the reactor thread on an empty pipe.
        while not stream.paused:
            try:
                chunk = os.read(stream.fd, _READ_SIZE)
            except BlockingIOError:
                return  # EAGAIN: pipe drained for now
            if not chunk:
                self.unregister(stream)  # EOF: child exited
                return
            stream.buf += chunk
            self._frame_pending(stream)

    def _frame_pending(self, stream: _Stream) -> None:
        # Frame complete lines out of buf, then submit ONE pool task per
        # read batch (review P1: per-line submits made every line after the
        # first a no-op token dance — the pool queue churn the reactor was
        # built to remove). The task loop drains whatever the queue holds.
        while True:
            nl = stream.buf.find(b"\n")
            if nl < 0:
                break
            line, _, stream.buf = stream.buf.partition(b"\n")
            stripped = bytes(line.strip())
            if stripped:
                with stream.lock:
                    stream.pending.append(stripped)
                    deep = len(stream.pending) >= _STREAM_BACKLOG
                    if deep and not stream.paused:
                        stream.paused = True
        if len(stream.buf) > _MAX_LINE_BYTES:
            # Keep the HEAD (the JSON "type" key lives there); the tail of an
            # unsalvageable >64MB line is the least valuable part.
            del stream.buf[_READ_SIZE:]
        # ALWAYS ensure a drain task exists when there is pending work: a
        # single read can burst past _STREAM_BACKLOG (paused flips on), and
        # skipping the submit would leave the queue full, inflight 0, and no
        # task to ever call _resume — the stream wedges until join times out
        # (codex review: 3000 one-byte lines in one write reproduces this).
        if stream.pending:
            self._submit_parse(stream)
        if stream.paused:
            # Backpressure by design (#578 point 3): stop reading this fd;
            # the child's pipe fills and the child blocks — events slow down
            # instead of being dropped or buffered unbounded.
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(stream.fd)

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
        """Pool task: drain the stream's pending queue, in order, exclusively
        (the ``writer`` token's holder loops until empty — see _Stream). A
        ``dropped`` stream is abandoned before the next open(): a late writer
        must not append old-attempt lines into a path a re-claimed execution
        may have truncated (#564 adjacency)."""
        with stream.lock:
            if stream.dropped or stream.writer:
                stream.inflight -= 1
                return
            stream.writer = True
        try:
            while True:
                batch = stream.take_pending()
                if not batch:
                    break
                if stream_dropped(stream):
                    break
                kept = [line for line in batch if _filter_line(line)]
                # Fence check INSIDE the write transaction window too: the
                # per-batch check above races a drop landing between it and
                # this open (review P2 residual). TOCTOU cannot be fully
                # closed without generation counters on the path — this
                # narrows it to the open→write gap only.
                if stream_dropped(stream):
                    break
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
        """Leaving-task completion check: finishing + empty queue + no task in
        flight ⇒ fire ``done``; final lines enqueued after the last skip get
        one more drain pass."""
        with stream.lock:
            stream.inflight -= 1
            finished = stream.finishing and not stream.pending and stream.inflight == 0
            requeued = stream.finishing and bool(stream.pending) and stream.inflight == 0
        if finished:
            stream.done.set()
        elif requeued:
            self._submit_parse(stream)

    def _drop(self, stream: _Stream) -> None:
        """Force-drop a wedged stream (join timed out): no final drain — the
        pool is the wedge point. ``dropped`` fences late writers (a re-claimed
        execution may have truncated the path by then)."""
        with self._streams_lock:
            self._streams.pop(stream.fd, None)
        with contextlib.suppress(KeyError, ValueError):
            self._selector.unregister(stream.fd)
        with stream.lock:
            stream.dropped = True
            stream.pending.clear()
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
            # Fence in-flight writers (same as _drop); buffered lines are
            # abandoned (module docstring), then a legacy pump takes the fd.
            # ``done`` fires from the pump thread's exit so join() does not
            # race the fallback drain (review P2: an immediate set let the
            # uploader read the file mid-flush — truncated events.jsonl).
            with stream.lock:
                stream.dropped = True
                stream.pending.clear()
            if not takeover_with_legacy_pump(stream):
                stream.done.set()  # no takeover: unblock joiners now
        self._pool.shutdown(wait=False)


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
    a reactor that is disabled (or fails at registration) also falls back to
    it — the pump surface, ``join(timeout)``, is identical either way."""
    if os.environ.get("AGENT_WORKER_EVENT_PUMP", "reactor") == "reactor":
        try:
            return EventPumpReactor.get().register(proc, output.name)
        except (ReactorUnavailable, OSError):
            print(
                f"event-pump reactor unavailable for {execution_id}; using thread pump",
                flush=True,
            )
            # _Stream.__init__ (reached when register failed AFTER stream
            # construction, e.g. the selector OSError arm) flipped the fd to
            # non-blocking for the reactor; the legacy pump's blocking
            # ``for raw in src`` iteration would see a spurious EAGAIN-as-EOF
            # on it — restore blocking mode before the fallback takes over.
            with contextlib.suppress(OSError):
                if proc.stdout is not None:
                    os.set_blocking(proc.stdout.fileno(), True)
    return spawn_event_pump(proc, output, f"pi-events-{execution_id[:8]}")
