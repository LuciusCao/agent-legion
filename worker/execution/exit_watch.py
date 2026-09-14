"""Event-driven child-exit supervision (#647 — #578 phases 2/3).

Phase 1 (#578/PR #586) moved the stdout pumps onto a selector reactor; the
exit waits stayed on the legacy shape — one pool thread per execution running
a 0.5s ``proc.wait(timeout=...)`` poll loop. At fleet scale that is N×2
wakeups/second of "look, nothing happened" (measured at #647: the largest
single supervision tax on the executor, kernel_task-heavy). This module does
to exit waits what the reactor did to pumps: ONE watcher thread receives
kernel exit notifications for every in-flight child, and each waiting caller
parks on its own ``threading.Event`` — zero wakeups while parked.

Kernel modes (``AGENT_WORKER_EXIT_WATCH=kqueue|pidfd|scan|auto``, default
auto — first available wins):

- kqueue (macOS/BSD): ``EVFILT_PROC`` + ``NOTE_EXIT`` per pid, ``EV_ONESHOT``
  so a fired knote never leaks; kqueue merges re-adds for a recycled pid.
- pidfd (Linux 5.3+): ``os.pidfd_open`` registered in a selector; readability
  is sticky after exit (even post-reap, and even if another thread reaped the
  child through ``Popen.poll`` — the heartbeat snapshot does exactly that).
- scan: the watcher's shared 0.5s tick ``poll()``s every registered child.
  Same tax shape as the legacy loop but ONE thread for the whole fleet; also
  the per-waiter fallback when a kernel registration fails (e.g. pidfd_open
  racing a reaped child).

Wake-always-carries-its-reason: every resolve sets a monotonic flag
(``exit_observed`` / ``timed_out`` / control events are set-once Events)
before setting ``done``, and the caller re-derives the verdict in the legacy
priority order (ownership_lost → cancelled → shutdown → timeout → exit).
Monotonicity makes this race-free — a reason true at wake time stays true at
verdict time. ``terminate`` still runs on the caller thread (grace-bounded
waits must not park the watcher).

Fail-closed: a watcher-thread error marks the reactor dead, resolves every
waiter immediately, and those callers degrade to the legacy per-execution
poll loop (``_poll_wait_locally`` — the pre-#647 semantics, kept ONLY as
this emergency exit; same fail-closed shape as the event-pump reactor's
legacy-pump takeover). New waits after death get a fresh singleton.

Phase-3 remainder (tracked follow-up): with exits event-driven the waiting
caller only parks, so ``run_execution`` could hand its post-exit tail to a
continuation instead of holding a thread — that rewrite (constant ~50
threads at any load) is deliberately out of scope here; this module plus the
idle-dying execution lane (``worker/execution/execution_lane.py``) removes
the wakeup tax and the never-shrink pool residue first.
"""

from __future__ import annotations

import contextlib
import os
import select
import selectors
import subprocess
import threading
import time

from worker.process_lifecycle import poll_wait_locally, terminate

# Shared tick: control events (ownership/cancel/shutdown) and scan-mode exits
# are re-checked on this cadence by the ONE watcher thread — the legacy loop's
# 0.5s per-execution re-check, paid once per process instead of per execution.
_TICK_SECONDS = 0.5
_MODE_ENV = "AGENT_WORKER_EXIT_WATCH"
_VALID_MODES = ("auto", "kqueue", "pidfd", "scan")


def _resolve_mode(requested: str) -> str:
    """Map the configured mode to a usable one; unavailable kernels (and
    unknown env values) degrade with a printed reason — an execution must
    not fail over a supervision-mode typo (same leniency as
    AGENT_WORKER_EVENT_PUMP)."""
    if requested not in _VALID_MODES:
        print(
            f"exit-watch: {_MODE_ENV}={requested} 非法（合法值 "
            f"{'|'.join(_VALID_MODES)}），按 auto 处理",
            flush=True,
        )
        requested = "auto"
    if requested == "scan":
        return "scan"
    if hasattr(select, "kqueue"):
        return "kqueue"
    if requested == "kqueue":
        print("exit-watch: kqueue requested but unavailable; using scan mode", flush=True)
    if hasattr(os, "pidfd_open"):
        return "pidfd"
    if requested == "pidfd":
        print("exit-watch: pidfd requested but unavailable; using scan mode", flush=True)
    return "scan"


class _Waiter:
    """One registered exit wait: the Popen, its deadline, the control events,
    and the resolution flags (all monotonic — see module docstring)."""

    __slots__ = (
        "proc",
        "deadline",
        "shutdown",
        "ownership_lost",
        "cancelled",
        "done",
        "exit_observed",
        "timed_out",
        "watcher_dead",
        "scan_only",
        "pidfd",
    )

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        timeout: float,
        shutdown: threading.Event,
        ownership_lost: threading.Event,
        cancelled: threading.Event | None,
    ) -> None:
        self.proc = proc
        self.deadline = time.monotonic() + timeout
        self.shutdown = shutdown
        self.ownership_lost = ownership_lost
        self.cancelled = cancelled
        self.done = threading.Event()
        self.exit_observed = False
        self.timed_out = False
        self.watcher_dead = False
        # Tick-polled instead of kernel-watched: a failed kernel
        # registration, or the whole scan mode.
        self.scan_only = False
        # pidfd-mode fd (applied/closed on the watcher thread only).
        self.pidfd = -1


class ExitWatchReactor:
    """Process-wide singleton: ``get()`` lazily starts the watcher thread;
    ``wait_for_exit`` mirrors the legacy ``process_lifecycle.wait_for_exit``
    contract (same signature, same return, same priority semantics)."""

    _singleton: ExitWatchReactor | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, mode: str = "auto") -> None:
        self._mode = _resolve_mode(mode)
        self._lock = threading.Lock()
        self._waiters: dict[int, _Waiter] = {}
        self._stop = threading.Event()
        self._failed = False
        # Deferred kernel-registration queue: pidfd selector mutations and fd
        # closes are applied on the watcher thread only (closing an fd another
        # thread is selecting on is the hazard; the wakeup pipe forces the
        # watcher back around to the queue promptly).
        self._pending: list[tuple[str, _Waiter]] = []
        self._kqueue: select.kqueue | None = None
        self._selector: selectors.BaseSelector | None = None
        self._wakeup_r = self._wakeup_w = -1
        if self._mode == "kqueue":
            self._kqueue = select.kqueue()
            self._wakeup_r, self._wakeup_w = os.pipe()
            os.set_blocking(self._wakeup_r, False)
            self._kqueue.control(
                [select.kevent(self._wakeup_r, select.KQ_FILTER_READ, select.KQ_EV_ADD)],
                0,
                0,
            )
        elif self._mode == "pidfd":
            self._selector = selectors.DefaultSelector()
            self._wakeup_r, self._wakeup_w = os.pipe()
            os.set_blocking(self._wakeup_r, False)
            self._selector.register(self._wakeup_r, selectors.EVENT_READ, data=None)
        print(f"exit-watch reactor started (mode={self._mode})", flush=True)
        self._thread = threading.Thread(target=self._watch_loop, name="exit-watch", daemon=True)
        self._thread.start()

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def get(cls) -> ExitWatchReactor:
        with cls._singleton_lock:
            if cls._singleton is None or cls._singleton.is_dead():
                cls._singleton = ExitWatchReactor(os.environ.get(_MODE_ENV, "auto"))
            return cls._singleton

    def is_dead(self) -> bool:
        return self._failed

    def mode(self) -> str:
        """The resolved kernel mode (observability: the slots heartbeat line
        prints it so ops can see kqueue/pidfd/scan at a glance)."""
        return self._mode

    def shutdown(self) -> None:
        """Stop the watcher (tests). Production never calls this: the thread
        is a daemon and the executor's shutdown Event reaches children
        through the tick like any other control event."""
        with ExitWatchReactor._singleton_lock:
            if ExitWatchReactor._singleton is self:
                ExitWatchReactor._singleton = None
        self._stop.set()
        self._wake()
        self._thread.join(timeout=5)
        if self._kqueue is not None:
            self._kqueue.close()
        if self._selector is not None:
            self._selector.close()
        for fd in (self._wakeup_r, self._wakeup_w):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)

    def _wake(self) -> None:
        if self._wakeup_w >= 0:
            with contextlib.suppress(OSError, BlockingIOError):
                os.write(self._wakeup_w, b"x")

    # -- registration ----------------------------------------------------

    def register(
        self,
        proc: subprocess.Popen[bytes],
        timeout: float,
        shutdown: threading.Event,
        ownership_lost: threading.Event,
        cancelled: threading.Event | None = None,
    ) -> _Waiter:
        waiter = _Waiter(proc, timeout, shutdown, ownership_lost, cancelled)
        with self._lock:
            if self._failed:
                waiter.watcher_dead = True
                waiter.done.set()
                return waiter
            self._waiters[proc.pid] = waiter
            if self._mode == "kqueue":
                # Cross-thread kqueue changelists are kernel-serialized; the
                # kqueue fd itself is never closed while the watcher lives.
                try:
                    assert self._kqueue is not None
                    self._kqueue.control(
                        [
                            select.kevent(
                                proc.pid,
                                select.KQ_FILTER_PROC,
                                select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                select.KQ_NOTE_EXIT,
                            )
                        ],
                        0,
                        0,
                    )
                except OSError:
                    waiter.scan_only = True
            elif self._mode == "pidfd":
                self._pending.append(("register", waiter))
            else:  # scan: no kernel watch — the shared tick polls it
                waiter.scan_only = True
        if self._mode == "pidfd":
            self._wake()
        return waiter

    def unregister(self, waiter: _Waiter) -> None:
        with self._lock:
            if self._waiters.get(waiter.proc.pid) is waiter:
                del self._waiters[waiter.proc.pid]
            if self._mode == "pidfd":
                # Applied (fd close included) on the watcher thread — see __init__.
                self._pending.append(("unregister", waiter))
        if self._mode == "pidfd":
            self._wake()

    # -- the wait itself ---------------------------------------------------

    def wait_for_exit(
        self,
        proc: subprocess.Popen[bytes],
        timeout: float,
        shutdown: threading.Event,
        shutdown_grace: float,
        ownership_lost: threading.Event,
        cancelled: threading.Event | None = None,
    ) -> tuple[int, bool]:
        """Drop-in replacement for the legacy polling ``wait_for_exit``:
        parks the caller on ``done`` (zero wakeups) while the watcher thread
        watches the kernel, the deadline, and the control events."""
        waiter = self.register(proc, timeout, shutdown, ownership_lost, cancelled)
        try:
            waiter.done.wait()
            # Verdict re-derivation in the legacy priority order; every
            # trigger is monotonic so a reason true at wake stays true.
            if waiter.watcher_dead:
                return poll_wait_locally(
                    proc,
                    max(0.0, waiter.deadline - time.monotonic()),
                    shutdown,
                    shutdown_grace,
                    ownership_lost,
                    cancelled,
                )
            if ownership_lost.is_set():
                terminate(proc, 5)
                return 1, False
            if cancelled is not None and cancelled.is_set():
                terminate(proc, 5)
                return 130, True
            if shutdown.is_set():
                terminate(proc, shutdown_grace)
                return 130, True
            if waiter.timed_out:
                terminate(proc, 5)
                return 124, True
            # exit_observed: the child is a zombie (or already reaped by the
            # heartbeat's poll) — wait() returns immediately.
            return proc.wait(), True
        finally:
            self.unregister(waiter)

    # -- watcher core ----------------------------------------------------

    def _watch_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self._apply_pending()
                timeout = self._tick_timeout()
                for waiter in self._wait_kernel(timeout):
                    waiter.exit_observed = True
                    waiter.done.set()
                self._tick()
        except Exception as exc:
            # #204 broad-except audit: watcher 存活语义——任何逃逸都不允许
            # 带崩 worker 进程，也不允许让已注册的等待者永远 park（那会把
            # 执行悬挂到租约过期）。吞是对的：fail-closed 降级——标记死亡、
            # 立即唤醒全部等待者（各自退回本地轮询），后续等待落到新单例。
            # 日志保全：print 异常与降级后果。
            print(
                f"exit-watch reactor failed, waiters degrade to local polling: {exc!r}", flush=True
            )
            self._fail_dead()

    def _apply_pending(self) -> None:
        """Watcher-thread-only selector mutations (pidfd mode)."""
        if self._mode != "pidfd":
            return
        with self._lock:
            pending, self._pending = self._pending, []
        for op, waiter in pending:
            if self._waiters.get(waiter.proc.pid) is not waiter and op == "register":
                continue  # unregistered while queued — nothing to arm
            if op == "register":
                try:
                    fd = os.pidfd_open(waiter.proc.pid)  # type: ignore[attr-defined]
                except OSError:
                    # Raced a reaped child (heartbeat poll) or unsupported —
                    # resolved now if it already exited, else tick-polled.
                    with self._lock:
                        if waiter.proc.poll() is not None:
                            waiter.exit_observed = True
                            waiter.done.set()
                        else:
                            waiter.scan_only = True
                    continue
                assert self._selector is not None
                waiter.pidfd = fd
                self._selector.register(fd, selectors.EVENT_READ, data=waiter)
            else:  # unregister
                fd = waiter.pidfd
                waiter.pidfd = -1
                if fd >= 0 and self._selector is not None:
                    with contextlib.suppress(KeyError, ValueError):
                        self._selector.unregister(fd)
                    with contextlib.suppress(OSError):
                        os.close(fd)

    def _tick_timeout(self) -> float:
        """select/kevent timeout: the shared tick, tightened to the nearest
        deadline so timeouts keep sub-tick precision (the legacy loop's
        ``min(0.5, remaining)`` equivalent)."""
        now = time.monotonic()
        with self._lock:
            deadlines = [waiter.deadline for waiter in self._waiters.values()]
        if not deadlines:
            return _TICK_SECONDS
        return max(0.0, min(min(deadlines) - now, _TICK_SECONDS))

    def _wait_kernel(self, timeout: float) -> list[_Waiter]:
        """Block for kernel exit events; returns the waiters whose child
        exited. Scan mode does its polling in the tick instead."""
        if self._mode == "kqueue":
            assert self._kqueue is not None
            exited: list[_Waiter] = []
            for event in self._kqueue.control(None, 256, timeout):
                if event.filter == select.KQ_FILTER_READ and event.ident == self._wakeup_r:
                    with contextlib.suppress(OSError, BlockingIOError):
                        os.read(self._wakeup_r, 65536)
                    continue
                if event.filter == select.KQ_FILTER_PROC:
                    with self._lock:
                        waiter = self._waiters.get(event.ident)
                    if waiter is not None:
                        exited.append(waiter)
            return exited
        if self._mode == "pidfd":
            assert self._selector is not None
            exited = []
            for key, _mask in self._selector.select(timeout):
                if key.data is None:
                    with contextlib.suppress(OSError, BlockingIOError):
                        os.read(self._wakeup_r, 65536)
                    continue
                exited.append(key.data)
            return exited
        time.sleep(timeout)
        return []

    def _tick(self) -> None:
        """The one shared 0.5s pass: control events, deadlines, scan-mode
        exits. Control wakes carry no flag — the caller re-derives them from
        the (monotonic) Events themselves."""
        now = time.monotonic()
        with self._lock:
            for waiter in self._waiters.values():
                if (
                    waiter.ownership_lost.is_set()
                    or (waiter.cancelled is not None and waiter.cancelled.is_set())
                    or waiter.shutdown.is_set()
                ):
                    waiter.done.set()
                elif waiter.deadline <= now:
                    waiter.timed_out = True
                    waiter.done.set()
                elif waiter.scan_only and waiter.proc.poll() is not None:
                    waiter.exit_observed = True
                    waiter.done.set()

    def _fail_dead(self) -> None:
        with self._lock:
            self._failed = True
            waiters = list(self._waiters.values())
        for waiter in waiters:
            waiter.watcher_dead = True
            waiter.done.set()


def wait_for_exit(
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
    cancelled: threading.Event | None = None,
) -> tuple[int, bool]:
    """Event-driven ``process_lifecycle.wait_for_exit`` (#647 phase 2) on the
    process-wide watcher; contract identical to the legacy function. The
    fail-closed degradation (watcher dead) is ``process_lifecycle.
    poll_wait_locally`` — the verbatim pre-#647 loop, kept as the only
    remaining copy."""
    return ExitWatchReactor.get().wait_for_exit(
        proc, timeout, shutdown, shutdown_grace, ownership_lost, cancelled
    )
