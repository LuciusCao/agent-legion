"""Group-commit batching for the result commit terminal writes (issue #591).

#569 retired the validate/unpack CPU segments (process pools) and left the
completion wave's remaining hotspot in the two DB-serializing segments the
issue's stage data names: ``lease_write`` (the four-table terminal
transaction, 59% of the slow-warning budget) and ``mark_done`` (the single-
row request UPDATE, 17%). Both are pure queueing: the same wave's
transactions fight for the same jobs-row lock (``sync_job_status``) and pay
one commit fsync each — the DB itself is idle (21% CPU, 45 idle
connections).

This module owns the write-side fix: one drain-only writer thread with a
group-commit queue. A commit thread parks its terminal write on the queue
and blocks on a per-item future (the GIL is released while waiting — and
psycopg already releases it for SQL I/O); the writer drains whatever
arrived, runs the ``finish`` items in ONE transaction and the ``mark_done``
items in another, then resolves the futures. Under a wave the jobs row is
locked once per drain round instead of once per result, and the two commits
become two per round. In idle rhythm the first item is drained immediately
— no added latency beyond one queue hop.

Ordering contract (the two-phase shape exists for it): a request's
``finish`` must land before its ``mark_done`` — the sweeper closes a claimed
request whose lease is no longer active when a crash leaves the pair split
(the direct path documents the same crash window). Batching per kind, with
the finish transaction always committed before the mark_done transaction in
a drain round, keeps that ordering for every interleaving the queue can
produce, including two attempts of the same execution reporting back to
back (the second ``mark_done`` returns None on the first's request-state
write, the same verdict the serial path gives).

Isolation contract: a failure inside one batched item must not fail its
neighbours. Every SQL body already guards per row (``finish_lease``
re-selects the lease and returns False on a non-active row; ``mark_done``
locks and re-checks its request row), so one item's 409 is data, not an
error. An unexpected exception aborts at most the transaction it fired in;
the writer then re-runs that slice's items ONE AT A TIME through the same
batched arm — a deterministic failure then fails only its own item (its
future carries the exception to the submitting commit thread, the same
raise the direct path gives), and every neighbour gets its true verdict.

Kill-switch: ``result_commit_batching=0`` bypasses the queue entirely (the
commit threads call the direct write paths, pre-#591 behavior); the thread
is only started when batching is on.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Items above which the writer splits a drain round into multiple batch
# transactions — a bound, not a target; drains naturally sit far below it.
# The point is fail-safe: one pathological round cannot hold the jobs-row
# lock (or a single transaction) over thousands of results, and the
# isolation fallback's worst case stays bounded.
MAX_ITEMS_PER_TRANSACTION = 64

_FINISH = "finish"
_MARK_DONE = "mark_done"


@dataclass
class _ItemFuture:
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    # Post-commit work the finish arm parks for the SUBMITTING thread (#591
    # C5: file scans never run on the single writer). Matched by queue
    # position: the arm returns verdicts plus one callback list per item.
    post_commit: list[Callable[[], None]] = field(default_factory=list)

    def resolve(self, value: Any, post_commit: list[Callable[[], None]] | None = None) -> None:
        self.result = value
        if post_commit:
            self.post_commit = post_commit
        self.done.set()

    def fail(self, exc: BaseException) -> None:
        self.error = exc
        self.done.set()


@dataclass
class _BatchItem:
    kind: str  # "finish" | "mark_done"
    args: tuple  # the batched arm's per-item argument tuple
    future: _ItemFuture = field(default_factory=_ItemFuture)


class ResultCommitBatcher:
    """Single-writer group-commit queue for the result terminal writes.

    ``finish_many`` / ``mark_done_many`` are the batched arms (the repo
    methods): each takes the drained argument tuples and returns one verdict
    per item, in order. The same arm with a single-item list is the
    isolation fallback, so batched and serial recovery share one code path
    per kind.
    """

    def __init__(
        self,
        finish_many: Callable[[list[tuple]], Any] | None = None,
        mark_done_many: Callable[[list[tuple]], Any] | None = None,
    ) -> None:
        # Late-bound arms: the plane constructs the batcher before the
        # repositories it wraps (both receive it at THEIR construction),
        # then assigns these. The writer thread starts only in the app
        # lifespan — after the plane is fully built — so the arms are
        # always bound before the first submit can run.
        self.finish_many = finish_many
        self.mark_done_many = mark_done_many
        self._queue: queue.SimpleQueue[_BatchItem | None] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # C6/#609 P1-1: the close gate — stop()'s {set + sentinel put} and
        # submit()'s {check + enqueue} pairs both run under _lock, so a
        # submitter either lands ahead of the sentinel or takes the direct
        # path; nothing can enqueue onto a dead writer. The writer's queue
        # consumption stays lock-free.
        self._closed = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._closed.is_set():
                # Restart after stop(): reopen the gate (a stopped batcher
                # otherwise stays in bypass mode forever) and drop any
                # sentinel a stop() without a live writer left behind — it
                # would kill the fresh writer on its first get(). Real
                # items cannot be parked behind the gate: post-close
                # submits never enqueue. A first start keeps the queue, so
                # submits that raced ahead of start() still drain.
                self._closed.clear()
                self._queue = queue.SimpleQueue()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._writer_loop, name="result-commit-batcher", daemon=True
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> None:
        """Stop the writer after draining; in-flight items complete first.

        The close decision and the sentinel enqueue run under the batcher
        lock, atomic against submit()'s check+enqueue pair (#609 P1-1): a
        submitter either lands its item ahead of the sentinel (the writer's
        exit drain resolves it) or observes the closed gate and takes the
        direct path — the interleaving where it enqueues behind a dead
        writer and parks on a future nobody resolves cannot occur.
        """
        with self._lock:
            self._closed.set()
            self._stop.set()
            self._queue.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)

    def submit(self, kind: str, args: tuple, *, direct: Callable[[], Any] | None = None) -> Any:
        """Queue one terminal write and block for its verdict.

        The item's verdict (or exception) crosses back through the future
        unchanged — the submitting commit thread observes exactly what the
        batched arm returned/raised for its item. When the future carries
        post-commit callbacks (the finish arm's events post-processing,
        #591 C5), the SUBMITTING thread runs them here, off the single
        writer — the direct path's parallel shape.

        After ``stop()`` the queue is closed (#591 C6): the closed-check
        and the enqueue run under the batcher lock, atomic against stop()'s
        close+sentinel pair (#609 P1-1) — a racing producer (an executor
        thread outliving its bounded shutdown wait) can never enqueue onto
        a drained queue and park forever. It runs the caller-provided
        ``direct`` serial path instead — the same writes, just unbatched.
        The writer never takes the lock (its queue consumption is
        lock-free), so submitters cannot stall a drain round.
        """
        item = _BatchItem(kind=kind, args=args)
        with self._lock:
            closed = self._closed.is_set()
            if not closed:
                self._queue.put(item)
        if closed:
            if direct is None:
                raise RuntimeError("result commit batcher is stopped")
            return direct()
        item.future.done.wait()
        if item.future.error is not None:
            raise item.future.error
        for callback in item.future.post_commit:
            _run_post_commit(callback)
        return item.future.result

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                self._stop.set()
                break
            batch = [item]
            # Drain-only collection: no sleep, no linger. When the queue is
            # empty the first item IS the batch — idle rhythm pays one queue
            # hop, nothing more. When a wave is landing, everything that
            # arrived meanwhile joins; the SQL I/O releases the GIL, so
            # submitters keep filling while the writer works.
            while len(batch) < MAX_ITEMS_PER_TRANSACTION:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._stop.set()
                    break
                batch.append(more)
            self._run_batch(batch)
        # Graceful shutdown: drain what submitters already queued (they are
        # blocked on futures and cannot requeue), then exit.
        self._drain_remaining()

    def _run_batch(self, batch: list[_BatchItem]) -> None:
        # Catch-all so no run_batch bug can kill the writer thread — a dead
        # writer strands every future submitter forever. The items' futures
        # carry the error to their commit threads (each becomes that
        # request's 500, retried by the Worker exactly like a direct-path
        # failure); the loop continues with the next round.
        try:
            finishes = [i for i in batch if i.kind == _FINISH]
            if finishes:
                self._run_kind(finishes, self._arm(self.finish_many))
            mark_doness = [i for i in batch if i.kind == _MARK_DONE]
            if mark_doness:
                self._run_kind(mark_doness, self._arm(self.mark_done_many))
        except Exception:
            # #204 broad-except audit: the catch-all exists so no run_batch
            # bug can kill the writer thread (a dead writer strands every
            # future submitter forever) — the failure space is the arms'
            # entire body, un-narrowable from here. The items' futures carry
            # the error to their commit threads (each becomes that
            # request's 500, retried by the Worker exactly like a
            # direct-path failure); the loop continues with the next round.
            logger.exception("result commit batcher round failed (%d items)", len(batch))
            for item in batch:
                if not item.future.done.is_set():
                    item.future.fail(RuntimeError("result commit batcher round failed"))

    @staticmethod
    def _arm(
        late_bound: Callable[[list[tuple]], Any] | None,
    ) -> Callable[[list[tuple]], Any]:
        """Narrow a late-bound arm to its run type (start() is the gate).

        ``_run_batch`` only ever runs on the writer thread, which ``start``
        launches strictly after the plane assigned both arms; a None here
        means the thread was started directly (tests) — fail loud at the
        first round instead of silently dropping items.
        """
        if late_bound is None:
            raise RuntimeError("batcher arm was never bound (plane wiring bug)")
        return late_bound

    def _run_kind(self, items: list[_BatchItem], batched_arm: Callable[[list[tuple]], Any]) -> None:
        """Run one kind's slice through the batched arm with isolation fallback.

        The finish arm returns ``(verdicts, per_item_post_commit)`` (#591
        C5 — file work goes back to the submitting thread); mark_done
        returns verdicts only. The single-item fallback passes through the
        same two shapes."""
        if not items:
            return
        try:
            verdicts, post_commit = _split_arm_result(batched_arm([item.args for item in items]))
        except Exception:
            # #204 broad-except audit: the whole slice is uncertain after a
            # shared-transaction abort (rollback), so this arm's job is
            # containment, not recovery — the outcome space is the arm's
            # entire body. Each item is re-run alone through the SAME arm;
            # a deterministic failure then fails exactly its own item
            # (its future carries the exception to the commit thread, the
            # same raise the direct path gives), and every neighbour gets
            # its true verdict. The fallback re-runs the arm per item, so
            # the failed round's post-commit work is regenerated too.
            logger.exception(
                "batched result commit failed (%d items); re-running serially", len(items)
            )
            for item in items:
                try:
                    single_v, single_post = _split_arm_result(batched_arm([item.args]))
                    item.future.resolve(single_v[0], single_post[0] if single_post else None)
                except Exception as exc:
                    # #204 broad-except audit: single-item isolation replay —
                    # the arm's whole body is the outcome space; a
                    # deterministic failure belongs to exactly this item.
                    item.future.fail(exc)
            return
        # strict (#609 review): an arm returning fewer verdicts than items
        # must fail the round (→ _run_batch's fail-all containment), never
        # leave tail futures parked on a future nobody resolves.
        for item, verdict, post in zip(items, verdicts, post_commit, strict=True):
            item.future.resolve(verdict, post)

    def _drain_remaining(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                continue
            self._run_batch([item])


def _split_arm_result(outcome: Any) -> tuple[list, list[list | None]]:
    """Normalize an arm's return into (verdicts, per-item post-commit lists).

    The finish arm returns ``(verdicts, callbacks)``; mark_done returns a
    bare verdict list (no post-commit work). Items without callbacks get
    an empty slot so positional matching against the queue order holds.
    """
    if isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(outcome[0], list):
        verdicts, callbacks = outcome
        per_item: list[list | None] = [None] * len(verdicts)
        for index, callback in enumerate(callbacks):
            if index < len(per_item):
                per_item[index] = [callback] if callback else None
        return verdicts, per_item
    return list(outcome), [None] * len(outcome)


def _run_post_commit(callback: Callable[[], None]) -> None:
    """One post-commit callback, contained: never-raise beside a committed
    terminal state (same contract the direct path's helpers carry)."""
    try:
        callback()
    except Exception:
        # #204 broad-except audit: best-effort telemetry/broadcast beside an
        # already-committed terminal state; losing it must not fail the
        # commit thread that already holds its verdict.
        logger.exception("result commit post-processing failed")


__all__ = ["MAX_ITEMS_PER_TRANSACTION", "ResultCommitBatcher"]
