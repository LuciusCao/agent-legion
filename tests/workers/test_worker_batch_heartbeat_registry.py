"""Focused state-transition tests for the extracted heartbeat registry."""

from __future__ import annotations

import threading

from worker.execution.heartbeat_batch import BatchHeartbeatRegistry as LegacyRegistry
from worker.execution.heartbeat_batch import _LeaseEntry as LegacyLeaseEntry
from worker.execution.heartbeat_registry import BatchHeartbeatRegistry, _LeaseEntry


def test_batch_module_preserves_registry_import_compatibility() -> None:
    assert LegacyRegistry is BatchHeartbeatRegistry
    assert LegacyLeaseEntry is _LeaseEntry


def test_registration_prune_and_snapshot_follow_attempt_identity() -> None:
    registry = BatchHeartbeatRegistry()
    old_lost = threading.Event()
    registry.register("exec-1", "lease-old", old_lost)
    new_lost = threading.Event()
    new_entry = registry.register("exec-1", "lease-new", new_lost)

    assert old_lost.is_set()
    assert not new_lost.is_set()
    registry.quiesce("exec-1", "lease-old")
    registry.prune("exec-1", "lease-old")
    assert registry.snapshot() == [new_entry]

    registry.quiesce("exec-1", "lease-new")
    assert registry.snapshot() == []
    registry.resume("exec-1", "lease-new")
    assert registry.snapshot() == [new_entry]
    registry.prune("exec-1", "lease-new")
    assert registry.snapshot() == []


def test_upload_registration_preserves_new_attempt_and_condemns_old_task() -> None:
    registry = BatchHeartbeatRegistry()
    new_lost = threading.Event()
    new_entry = registry.register("exec-1", "lease-new", new_lost)
    old_lost = threading.Event()

    returned = registry.register_upload("exec-1", "lease-old", old_lost)

    assert returned is new_entry
    assert old_lost.is_set()
    assert not new_lost.is_set()
    assert registry.snapshot() == [new_entry]


def test_upload_rebind_converges_late_snapshot_verdict_on_task_event() -> None:
    registry = BatchHeartbeatRegistry()
    executor_lost = threading.Event()
    registry.register("exec-1", "lease-1", executor_lost)
    in_flight = registry.snapshot()[0]
    task_lost = threading.Event()

    entry = registry.register_upload("exec-1", "lease-1", task_lost)
    in_flight.ownership_lost.set()

    assert in_flight is not entry
    assert task_lost.is_set()
    assert entry.ownership_lost is task_lost
    assert registry.snapshot() == []


def test_lost_and_settled_verdicts_have_distinct_terminal_effects() -> None:
    registry = BatchHeartbeatRegistry()
    lost = threading.Event()
    registry.register("exec-lost", "lease-lost", lost)
    settled = threading.Event()
    registry.register("exec-settled", "lease-settled", settled)
    reclaimed = threading.Event()
    current = registry.register("exec-reclaimed", "lease-new", reclaimed)

    registry.apply_beat_result(
        lost=[("exec-lost", "lease-lost"), ("exec-reclaimed", "lease-old")],
        cancelled=[],
        settled=["exec-settled"],
    )

    assert lost.is_set()
    assert not settled.is_set()
    assert not reclaimed.is_set()
    assert registry.snapshot() == [current]
