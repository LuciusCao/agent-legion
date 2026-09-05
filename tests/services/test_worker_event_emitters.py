"""Structured worker-path events (issue #490): Host-side emitter, claim
reason mapping, and the offline-transition detector.

The events are single-line JSON on the ``agent_legion.worker_events`` logger;
these tests pin the event-name full set, the reason mapping (claim.evaluate
skip reasons → claim.rejected vs claim.empty), the 502-era facts (status code
+ target URL on the worker side live in tests/workers/test_worker_events.py),
and the ops-metrics rider's online→offline transition discipline (seed, never
fire on restart; one event per transition).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from server.app.agent_broker.worker_events import (
    _KNOWN_EVENTS,
    _REJECT_REASONS,
    WorkerOfflineDetector,
    emit_worker_event,
    note_skip_reasons,
)

pytestmark = pytest.mark.no_db


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Live view of this logger's captured records (caplog.records grows
    during the test; a fixture must NOT return a snapshot of the list)."""
    caplog.set_level(logging.DEBUG, logger="agent_legion.worker_events")

    class _View(list):  # thin wrapper: attribute access proxies the live list
        @property
        def _live(self) -> list[logging.LogRecord]:
            return [
                record for record in caplog.records if record.name == "agent_legion.worker_events"
            ]

        def __len__(self) -> int:
            return len(self._live)

        def __getitem__(self, index):
            return self._live[index]

        def __iter__(self):
            return iter(self._live)

    return _View()


def _json_records(records: list[logging.LogRecord]) -> list[dict]:
    return [json.loads(record.getMessage()) for record in records]


def test_event_name_full_set_is_pinned() -> None:
    # runbook §7 documents each of these; a rename must update the runbook
    # and these two frozensets together.
    assert {
        "worker.registered",
        "worker.offline",
        "claim.granted",
        "claim.empty",
        "claim.rejected",
        "execution.started",
        "execution.finished",
        "execution.heartbeat_rejected",
        "execution.lease_expired",
    } == _KNOWN_EVENTS


def test_emit_event_is_single_line_json_with_ts_and_level(events) -> None:
    emit_worker_event("worker.offline", {"worker_id": "home-mini"})
    payload = _json_records(events)[-1]
    assert payload["event"] == "worker.offline"
    assert payload["worker_id"] == "home-mini"
    # ts parses as UTC ISO-8601 (the cross-machine alignment key).
    datetime.fromisoformat(payload["ts"])
    # offline is a transition event: INFO level even without DEBUG enabled.
    assert events[-1].levelno == logging.INFO


def test_emit_event_normal_rhythm_is_debug(events) -> None:
    emit_worker_event("claim.granted", {"worker_id": "w"})
    emit_worker_event("claim.empty", None)
    emit_worker_event("execution.finished", {"outcome": "completed"})
    assert [record.levelno for record in events] == [logging.DEBUG] * 3


def test_emit_event_unknown_name_warns_but_emits(events) -> None:
    emit_worker_event("not.a.known.event", {"x": 1})
    # The line is emitted twice: once as the WARNING call-out, once as the
    # actual event line (INFO).
    warn_records = [r for r in events if r.levelno == logging.WARNING]
    info_records = [r for r in events if r.levelno == logging.INFO]
    assert "unregistered name" in warn_records[-1].getMessage()
    assert _json_records(info_records)[-1]["event"] == "not.a.known.event"


def test_note_skip_reasons_maps_admission_rejections() -> None:
    # The four admission-mismatch families from the issue map to rejected:
    # 并发池满 / runtime 不匹配 / model 未声明 / scope 拒绝（claim_evaluate
    # 判定点命名）。
    rejected, reasons = note_skip_reasons(
        "w", {"capacity_full": 2, "runtime_mismatch": 1, "zero_count": 0}
    )
    assert rejected is True
    assert reasons == {"capacity_full": 2, "runtime_mismatch": 1}


def test_note_skip_reasons_passthrough_only_is_empty_not_rejected() -> None:
    # Skip reasons that are NOT this-worker admission (paused workspace,
    # contract invalid, lock races…) stay claim.empty with the reasons riding
    # the event — an operator still sees WHY the queue head was skipped.
    rejected, reasons = note_skip_reasons("w", {"workspace_paused": 3, "lock_raced": 1})
    assert rejected is False
    assert reasons == {"workspace_paused": 3, "lock_raced": 1}


def test_note_skip_reasons_empty_queue_is_plain_empty() -> None:
    rejected, reasons = note_skip_reasons("w", None)
    assert rejected is False
    assert reasons == {}


def test_reject_reason_covers_the_four_issue_families() -> None:
    # concurrency_full / runtime_mismatch / model_not_declared / scope_denied
    # in the issue's wording map onto the claim_evaluate decision points:
    assert {
        "capacity_full",
        "code_capacity_full",
        "capacity_raced",
        "runtime_mismatch",
        "model_mismatch",
        "workspace_not_allowed",
    } <= _REJECT_REASONS


# ---------------------------------------------------------------------------
# offline-transition detector (worker_events.WorkerOfflineDetector, riding
# the ops-metrics sampling pass)


def _detector() -> WorkerOfflineDetector:
    return WorkerOfflineDetector()


def _note(detector: WorkerOfflineDetector, now: datetime, seen: dict[str, datetime | None]):
    detector.note(now, seen)


def test_offline_first_bucket_seeds_without_firing(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    assert detector._known_workers == {"home-mini": now}
    assert not [record for record in events if "worker.offline" in record.getMessage()]


def test_offline_transition_fires_once_with_last_seen(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    _note(detector, now + timedelta(minutes=1), {})  # went silent
    payload = _json_records(events)[-1]
    assert payload["event"] == "worker.offline"
    assert payload["worker_id"] == "home-mini"
    assert payload["last_seen_at"] == now.isoformat()
    assert payload["threshold_seconds"] == 30
    # Second silent bucket: the memo was popped, no repeat event.
    _note(detector, now + timedelta(minutes=2), {})
    assert len([r for r in events if '"worker.offline"' in r.getMessage()]) == 1


def test_offline_recovery_rearms_the_transition(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    _note(detector, now + timedelta(minutes=1), {"home-mini": now + timedelta(minutes=1)})
    _note(detector, now + timedelta(minutes=2), {})  # first offline
    _note(detector, now + timedelta(minutes=3), {})  # no repeat
    _note(detector, now + timedelta(minutes=4), {"home-mini": now + timedelta(minutes=4)})
    _note(detector, now + timedelta(minutes=5), {})  # second offline fires again
    offline_events = [r for r in events if '"worker.offline"' in r.getMessage()]
    assert len(offline_events) == 2


def test_offline_handles_naive_and_string_timestamps(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    # DB row factories may render timestamptz naive (session tz) or as string.
    _note(detector, now, {"a": now.replace(tzinfo=None), "b": now.isoformat()})
    assert set(detector._known_workers) == {"a", "b"}
    _note(detector, now + timedelta(minutes=1), {})
    assert {p["worker_id"] for p in _json_records(events)} == {"a", "b"}


def test_offline_never_raises_on_malformed_input(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    # A bad timestamp string must not take the sampling pass down.
    _note(detector, now, {"bad": "not-a-datetime"})
    assert detector._known_workers == {}
