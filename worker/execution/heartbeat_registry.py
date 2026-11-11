"""Thread-safe lease registry for the per-Worker heartbeat coordinator.

The registry owns lease identity, upload handover, attempt displacement,
quiesce/adoption state, verdict application, and beatable snapshots. The
batch and degraded coordinators consume snapshots without owning any of
those lifecycle transitions.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _LeaseEntry:
    """One registered lease: identity plus the shared single-beat state."""

    execution_id: str
    lease_id: str
    ownership_lost: threading.Event
    on_cancelled: Callable[[list[str]], Any] | None = None
    proc_ref: dict[str, subprocess.Popen[bytes] | None] = field(
        default_factory=lambda: {"proc": None}
    )
    adopted: threading.Event = field(default_factory=threading.Event)
    # Beats paused (final result report in flight): a single beat racing the
    # commit logs a spurious "lost ownership" 409. Advisory only — the flag
    # is set outside the registry lock, so a beat already snapshotted may
    # still race the report; the worst case is that one spurious 409 log
    # line, never a wrong renewal (the Host-side predicate is authoritative).
    quiesced: bool = False


class BatchHeartbeatRegistry:
    """Thread-safe registry of this Worker's live leases.

    ``register`` at claim time (or upload restore), ``prune`` when the lease
    is done, ``quiesce``/``resume`` around the final result report. The
    per-Worker coordinator thread turns the registry into one batched beat
    per interval. The executor-side facade (``execution/heartbeat.py``) and
    the upload-side helpers (``upload/heartbeat.py``) are the only callers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _LeaseEntry] = {}
        # Set once the Host turned out to predate the batch endpoint; the
        # loop then beats per execution from the same registry.
        self.degraded_to_single = False

    def _matching_entry(self, execution_id: str, lease_id: str) -> _LeaseEntry | None:
        """Caller holds ``_lock``: the entry for THIS (execution, lease) pair.

        Keyed by execution_id but matched by the pair: a Host requeue can put
        a NEW lease under the same execution_id before the old attempt's
        cleanup arrives, and that entry belongs to the new attempt — the old
        attempt's calls must find nothing (its lease_id no longer matches)
        and touch nothing."""
        entry = self._entries.get(execution_id)
        if entry is not None and entry.lease_id != lease_id:
            return None
        return entry

    def register(
        self,
        execution_id: str,
        lease_id: str,
        ownership_lost: threading.Event,
        on_cancelled: Callable[[list[str]], Any] | None = None,
    ) -> _LeaseEntry:
        """The executor arm's claim-time register: OVERWRITES any entry under
        this execution_id (claim time owns the slot — a Host requeue + this
        Worker's re-claim replaces the old attempt's entry wholesale).

        #644 attack review: an entry being displaced here belongs to a lease
        the Host has already reassigned, and that lease may still have a
        live consumer on the upload side (an armed queued task whose
        pair-matched quiesce/resume can never match again, and whose lease
        will never appear in another beat — the new snapshot carries only
        the NEW lease). By definition that old lease is dead: fire the
        displaced entry's ownership_lost so the old task's report loop
        takes its terminal abandon instead of retrying to the 60s cap
        forever under a report-plane partition (pinning upload lanes).
        Same lease never lands here — the rebind path is register_upload's
        own branch — and the incoming claim's event is never touched, so a
        healthy re-claim cannot be mis-condemned (isomorphic to the
        mismatch-arm argument in ``register_upload``)."""
        entry = _LeaseEntry(
            execution_id=execution_id,
            lease_id=lease_id,
            ownership_lost=ownership_lost,
            on_cancelled=on_cancelled,
        )
        with self._lock:
            current = self._entries.get(execution_id)
            if current is not None and current is not entry:
                current.ownership_lost.set()
            self._entries[execution_id] = entry
        return entry

    def register_upload(
        self,
        execution_id: str,
        lease_id: str,
        ownership_lost: threading.Event,
    ) -> _LeaseEntry:
        """#644: the upload arm's register — NEVER displaces a re-claimed
        attempt's entry, and CONDEMNS an old task arming against one.

        The executor arm's ``register`` must overwrite a requeued execution's
        OLD entry with the new lease (claim time owns the slot). This arm arms
        a queued upload task, which may be the OLD attempt's task racing the
        re-claim: its displacing register deleted the new attempt's entry and
        the old attempt's pair-matched prune then removed its own — the
        execution ended up with NO entry, its new lease silently expired
        unrenewed (result loss + requeue spiral). Same-lease replacement (the
        executor→upload handover) stays: it rebinds the entry to the task's
        shared ownership_lost event (#644).

        A lease MISMATCH here means the registry already holds the re-claimed
        attempt's entry — the incoming task's lease is by definition gone
        (the Host reassigned it). The old task is condemned on the spot
        (#644 review): its pair-matched quiesce/resume can never find an
        entry, and no beat will ever return a lost verdict for the dead
        lease, so without this its report loop would retry to the 60s cap
        forever, pinning upload lanes (the storm engine this PR fixes). The
        registry itself stays untouched — the new entry is returned as-is.

        Same-lease rebind (the executor→upload handover) inherits the
        displaced entry's terminal state (#644 attack review): a lost
        verdict that landed on the executor-era entry — during the
        adopt→submit gap or in flight against a snapshotted entry while the
        rebound entry was being installed — must not vanish with the old
        entry object, or the task's report loop would retry to the cap
        forever under a report-plane partition. quiesce is deliberately NOT
        inherited: the report lane owns that flag from here on (the rebind
        happens before the first quiesce). proc_ref/adopted are inherited
        (the adopt semantics ride the entry, not the caller's timing — a
        rebind before adopt must not resurrect the zombie stop).
        #644 codex3 P2: the rebind additionally REDIRECTS the displaced
        entry's event field to the caller's (task's) event object before
        the displaced object leaves the registry. A beat already in flight
        against the displaced object (batch chunk, degraded single-beat
        thread, relay round racing the snapshot) sets
        ``entry.ownership_lost`` when its response ARRIVES — after this
        rebind already read ``is_set()`` — and without the redirect that
        late set lands on the executor-era event object, which the task's
        ``_report`` never polls (the state inheritance above cannot see a
        verdict that has not landed yet). The entry's own event stays the
        caller's (the task wiring is unchanged); only the displaced
        object's field is pointed at it, so every late set converges on the
        event the delivery plane polls. Events are never cleared, so the
        redirect is safe in every branch: on an already-set caller event
        the late set is a no-op, and a torn read (setter grabbed the old
        object just before the swap) is covered by the next beat round —
        the Host's per-item predicate is idempotent and re-judges the same
        dead lease, and a lease that left the beat plane entirely is
        condemned at displacement by ``register``."""
        entry = _LeaseEntry(
            execution_id=execution_id,
            lease_id=lease_id,
            ownership_lost=ownership_lost,
        )
        with self._lock:
            current = self._entries.get(execution_id)
            if current is not None and current.lease_id != lease_id:
                # A re-claimed attempt owns the slot: keep its entry, but the
                # incoming (old) task must not outlive its dead lease — fire
                # its terminal event so _report's next ownership check
                # abandons the moot delivery instead of retrying.
                ownership_lost.set()
                return current
            if current is not None:
                # #644 attack review: the handover rebind — inherit the
                # executor-era entry's terminal verdict (a beat 409 that
                # landed on it during the adopt→submit gap or in flight
                # against the snapshotted old entry object; without the
                # inheritance the verdict dies with the old entry and the
                # task retries forever). Events are idempotent — set() on a
                # fresh event for a live lease is a no-op that cannot fire.
                if current.ownership_lost.is_set():
                    ownership_lost.set()
                # codex3 P2: redirect the displaced object's event field to
                # the caller's event so the in-flight verdict lands on the
                # event _report polls (see method docstring).
                current.ownership_lost = ownership_lost
                entry.proc_ref = current.proc_ref
                if current.adopted.is_set():
                    entry.adopted.set()
            self._entries[execution_id] = entry
            return entry

    def prune(self, execution_id: str, lease_id: str) -> None:
        """Stop beating one lease (result delivered, ownership lost, discard).

        Pair-matched: a re-claimed execution's entry (new lease) survives an
        old attempt's late prune."""
        with self._lock:
            if self._matching_entry(execution_id, lease_id) is not None:
                del self._entries[execution_id]

    def quiesce(self, execution_id: str, lease_id: str) -> None:
        with self._lock:
            entry = self._matching_entry(execution_id, lease_id)
            if entry is not None:
                entry.quiesced = True

    def resume(self, execution_id: str, lease_id: str) -> None:
        """Resume beats after a transient report failure backs off."""
        with self._lock:
            entry = self._matching_entry(execution_id, lease_id)
            if entry is not None:
                entry.quiesced = False

    def set_adopted(self, execution_id: str, lease_id: str) -> None:
        """Upload adoption: beats must outlive the exited agent process."""
        with self._lock:
            entry = self._matching_entry(execution_id, lease_id)
            if entry is not None:
                entry.adopted.set()

    def prune_settled(self, settled: list[str]) -> None:
        """Quietly drop settled executions from the registry (#590).

        The Host classified these refusals inside the beat transaction as
        the completion followup (the execution row is in a TERMINAL state);
        unlike ``apply_beat_result``'s lost arm there is no ownership_lost
        to set and no signal to the executor — the entry simply stops being
        beaten. NOT pair-matched by design: terminal is absorbing (no path
        requeues a done/cancelled row), so no live new-attempt entry can
        exist under that execution_id and the next snapshot must not carry
        the dead lease whatever its lease_id was."""
        if not settled:
            return
        with self._lock:
            for execution_id in settled:
                self._entries.pop(execution_id, None)

    def apply_beat_result(
        self, lost: list[tuple[str, str]], cancelled: list[str], settled: list[str] | None = None
    ) -> None:
        """Apply the supervisor relay's beat verdicts (#566 phase 2).

        Pair-matched like every other mutation: a lost verdict for a lease
        the Host already requeued and this Worker re-claimed names the OLD
        lease_id, so the new attempt's entry matches nothing and stays
        untouched. Cancelled fan-out dedups by callback identity (every
        entry's callback is or wraps the same cancel_executions)."""
        self.prune_settled(settled or [])
        with self._lock:
            entries = list(self._entries.values())
            for execution_id, lease_id in lost:
                entry = self._matching_entry(execution_id, lease_id)
                if entry is not None:
                    entry.ownership_lost.set()
        if cancelled:
            delivered: set[int] = set()
            for entry in entries:
                callback = entry.on_cancelled
                if callback is None or id(callback) in delivered:
                    continue
                delivered.add(id(callback))
                callback(cancelled)

    def snapshot(self) -> list[_LeaseEntry]:
        """Beatable entries: alive, not quiesced, not already lost.

        Entries whose agent process exited unadopted are pruned here (the
        per-execution loop's zombie stop): the Host's orphan sweeper must be
        able to reclaim that lease instead of the Worker hiding a zombie."""
        snapshot: list[_LeaseEntry] = []
        with self._lock:
            for execution_id, entry in list(self._entries.items()):
                proc = entry.proc_ref["proc"]
                if proc is not None and proc.poll() is not None and not entry.adopted.is_set():
                    del self._entries[execution_id]
                    print(
                        f"heartbeat stopping for {execution_id}: agent process exited unadopted",
                        flush=True,
                    )
                    continue
                if entry.quiesced or entry.ownership_lost.is_set():
                    continue
                snapshot.append(entry)
        return snapshot
