"""Structured worker-path events (issue #490): Host-side emitter, claim
reason mapping, and the offline-transition detector.

The events are single-line JSON on the ``agent_legion.worker_events`` logger;
these tests pin the event-name full set, the reason mapping (claim.evaluate
skip reasons → claim.rejected vs claim.empty), the 502-era facts (status code
+ target URL on the worker side live in tests/workers/test_worker_events.py),
and the ops-metrics rider's online→offline transition discipline (seed, never
fire on restart; one event per transition).

The offline tests feed the PRODUCTION input shape — the full unrevoked
last_seen map with a worker present-but-stale — because the original tests
fed an empty map to mean "went silent"; in production the row stays in the
map and only the 30 s threshold decides (that divergence let the threshold
bug ship green). A caplog-independent visibility test (no handler attached
by pytest — the log-config logger tree is exercised for real) pins that the
INFO events reach a configured handler, the gap that hid all Host-side
events in production.
"""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.agent_broker.worker_events import (
    _KNOWN_EVENTS,
    _REJECT_REASONS,
    ONLINE_THRESHOLD_SECONDS,
    WorkerOfflineDetector,
    as_utc,
    emit_worker_event,
    note_skip_reasons,
)

pytestmark = pytest.mark.no_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_CONFIG_PATH = _REPO_ROOT / "deploy" / "uvicorn-log-config.json"


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
        "worker.register_rejected",
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
    # Non-rejection reasons riding claim.empty stay DEBUG (workspace_paused
    # is the dev-shape norm; promotion would be a per-minute INFO noise
    # source). Admission rejections route to claim.rejected (INFO).
    emit_worker_event("claim.empty", {"worker_id": "w", "reasons": {"workspace_paused": 3}})
    emit_worker_event("execution.finished", {"outcome": "completed"})
    assert [record.levelno for record in events] == [logging.DEBUG] * 4


def test_emit_event_unknown_name_warns_once_without_duplicate_emission(events) -> None:
    # P2-3: the unknown-name call-out carries the full line (payload
    # included) at WARNING; the event itself is dropped — the pre-fix code
    # logged the same line twice (WARNING + INFO).
    emit_worker_event("not.a.known.event", {"x": 1})
    records = list(events)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "unregistered name" in records[0].getMessage()
    assert '"x": 1' in records[0].getMessage()


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
# payload builders (never-raise contract)


class _ManifestClaim:
    """Minimal AgentClaim stand-in: only the fields note_claim_outcome reads."""

    def __init__(self, manifest: dict) -> None:
        self.execution_id = "e1"
        self.workspace_id = "ws-1"
        self.job_id = "job-1"
        self.node_key = "node"
        self.kind = "agent"
        self.runtime = ""
        self.manifest = manifest


class _View:
    agent_active = 0
    agent_capacity = 4
    code_active = 0
    code_capacity = 0


def test_note_claim_outcome_survives_malformed_execution_manifest(events) -> None:
    """P1-1: manifest['execution'] may be None / a string / absent (the
    manifest is caller-built JSON) — the payload builder must degrade, not
    raise into the claim transaction's exits."""
    from server.app.agent_broker.worker_events import note_claim_outcome

    for manifest in ({"execution": None}, {"execution": "bogus"}, {}):
        note_claim_outcome("w", _ManifestClaim(manifest), _View(), {})  # must not raise
    granted = [p for p in _json_records(events) if p["event"] == "claim.granted"]
    assert len(granted) == 3
    assert all(p["model"] == "" for p in granted)


def test_note_claim_outcome_reads_model_from_execution_manifest(events) -> None:
    from server.app.agent_broker.worker_events import note_claim_outcome

    note_claim_outcome("w", _ManifestClaim({"execution": {"model": "test-model"}}), _View(), {})
    granted = _json_records(events)[-1]
    assert granted["event"] == "claim.granted"
    assert granted["model"] == "test-model"


class _RegisterPayload:
    """RegisterAgentWorkerRequest stand-in (only the fields the emitters read)."""

    def __init__(self, **overrides: object) -> None:
        self.worker_id = "w-1"
        self.name = "mini"
        self.protocol_version = 3
        self.runtimes = ["pi"]
        self.runtime_versions = {}
        self.max_concurrency = 4
        self.max_code_concurrency = 2
        for key, value in overrides.items():
            setattr(self, key, value)


def test_note_worker_register_rejected_emits_reason(events) -> None:
    from server.app.agent_broker.worker_events import note_worker_register_rejected

    note_worker_register_rejected(_RegisterPayload(), "protocol_version_too_old", 3)
    payload = _json_records(events)[-1]
    assert payload["event"] == "worker.register_rejected"
    assert payload["worker_id"] == "w-1"
    assert payload["protocol_version"] == 3
    assert payload["reason"] == "protocol_version_too_old"
    assert payload["min_protocol_version"] == 3
    assert events[-1].levelno == logging.INFO

    note_worker_register_rejected(_RegisterPayload(), "register_key_deleted", None)
    payload = _json_records(events)[-1]
    assert "min_protocol_version" not in payload


def test_note_worker_register_rejected_never_raises(events) -> None:
    from server.app.agent_broker.worker_events import note_worker_register_rejected

    class _Broken:
        @property
        def worker_id(self) -> str:
            raise RuntimeError("boom")

    note_worker_register_rejected(_Broken(), "invalid_registration", None)  # must not raise
    assert not [r for r in events if "register_rejected" in r.getMessage()]


# ---------------------------------------------------------------------------
# offline-transition detector (worker_events.WorkerOfflineDetector, riding
# the ops-metrics sampling pass)


def _detector() -> WorkerOfflineDetector:
    return WorkerOfflineDetector()


def _note(
    detector: WorkerOfflineDetector,
    now: datetime,
    seen: dict[str, datetime | None],
):
    """Feed one bucket in the PRODUCTION shape: the full unrevoked last_seen
    map plus the sampling pass's online threshold (now − 30 s)."""
    online_since = now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    detector.note(seen, online_since)


def test_offline_first_bucket_seeds_without_firing(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    assert detector._known_workers == {"home-mini": now}
    assert not [record for record in events if "worker.offline" in record.getMessage()]


def test_offline_fires_when_stale_worker_stays_in_map(events) -> None:
    """The production shape: a worker going silent KEEPS its row (and its
    last_seen cell) in the unrevoked map — only the 30 s threshold crosses.
    The pre-fix detector treated map presence as online, so production
    offline events never fired (P0 on the #490 review)."""
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    # Next bucket: still in the map, last_seen is now 90 s old.
    _note(detector, now + timedelta(minutes=1), {"home-mini": now})
    payload = _json_records(events)[-1]
    assert payload["event"] == "worker.offline"
    assert payload["worker_id"] == "home-mini"
    assert payload["last_seen_at"] == now.isoformat()
    assert payload["threshold_seconds"] == ONLINE_THRESHOLD_SECONDS
    # Still in the map the bucket after: memo was popped, no repeat event.
    _note(detector, now + timedelta(minutes=2), {"home-mini": now})
    assert len([r for r in events if '"worker.offline"' in r.getMessage()]) == 1


def test_offline_recovery_rearms_the_transition(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now})
    _note(detector, now + timedelta(minutes=1), {"home-mini": now + timedelta(minutes=1)})
    _note(detector, now + timedelta(minutes=2), {"home-mini": now + timedelta(minutes=1)})  # stale
    _note(
        detector, now + timedelta(minutes=3), {"home-mini": now + timedelta(minutes=1)}
    )  # no repeat
    _note(detector, now + timedelta(minutes=4), {"home-mini": now + timedelta(minutes=4)})
    _note(
        detector, now + timedelta(minutes=5), {"home-mini": now + timedelta(minutes=4)}
    )  # 2nd offline
    offline_events = [r for r in events if '"worker.offline"' in r.getMessage()]
    assert len(offline_events) == 2


def test_offline_event_last_seen_is_the_db_truth_not_first_observation(events) -> None:
    """The memo stores the worker's true DB last_seen_at — NOT the first
    bucket it was observed online. The pre-fix code stored sampled_at, so
    the offline event's last_seen_at lied («first seen» instead of «last
    seen»)."""
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    seen_at = now - timedelta(seconds=10)  # DB truth predates the bucket
    _note(detector, now, {"home-mini": seen_at})
    _note(detector, now + timedelta(minutes=1), {"home-mini": seen_at})
    payload = _json_records(events)[-1]
    assert payload["event"] == "worker.offline"
    assert payload["last_seen_at"] == seen_at.isoformat()
    assert payload["last_seen_at"] != now.isoformat()


def test_offline_worker_vanishing_from_map_is_not_a_health_event(events) -> None:
    """A worker leaving the map entirely between buckets (row deleted /
    revoked) is a management action — no offline event; only the threshold
    crossing fires."""
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"home-mini": now, "removed": now})
    # 'removed' gone from the map; home-mini still fresh.
    _note(detector, now + timedelta(minutes=1), {"home-mini": now + timedelta(minutes=1)})
    assert not [r for r in events if '"worker.offline"' in r.getMessage()]
    assert set(detector._known_workers) == {"home-mini"}


def test_offline_never_fires_for_a_worker_stale_before_first_observation(events) -> None:
    # A worker already past the threshold when first seen (Host restart with
    # a stale fleet) seeds nothing — the first bucket after a restart only
    # seeds, never fires.
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    _note(detector, now, {"ghost": now - timedelta(minutes=5)})
    assert detector._known_workers == {}
    assert not [r for r in events if '"worker.offline"' in r.getMessage()]


def test_offline_handles_naive_and_string_timestamps(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    # DB row factories may render timestamptz naive (session tz) or as string.
    seen = {"a": now.replace(tzinfo=None), "b": now.isoformat()}
    _note(detector, now, seen)
    assert set(detector._known_workers) == {"a", "b"}
    # Still present next bucket, timestamps unchanged (stale) — the raw
    # cell shapes must survive the coercion on the offline path too.
    _note(detector, now + timedelta(minutes=1), seen)
    assert {p["worker_id"] for p in _json_records(events)} == {"a", "b"}


def test_offline_never_raises_on_malformed_input(events) -> None:
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    # A bad timestamp string must not take the sampling pass down.
    _note(detector, now, {"bad": "not-a-datetime"})
    assert detector._known_workers == {}


def test_offline_borderline_last_seen_exactly_at_threshold_stays_online(events) -> None:
    # as_utc(last_seen) == online_since is ">= threshold" → online (the
    # ops online count uses the same inclusive comparison).
    detector = _detector()
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    online_since = now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    _note(detector, now, {"edge": online_since})
    assert set(detector._known_workers) == {"edge"}


def test_as_utc_coerces_and_rejects() -> None:
    now = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    assert as_utc(now) == now
    assert as_utc(now.isoformat()) == now
    assert as_utc(now.replace(tzinfo=None)) == now
    assert as_utc(None) is None
    assert as_utc("not-a-datetime") is None


# ---------------------------------------------------------------------------
# Production visibility (P0-3 on the #490 review): the events must survive
# the uvicorn log-config, not just caplog — pytest's caplog attaches its own
# handler to the root logger, which masked the production gap where
# `agent_legion.worker_events` INFO lines had NO handler at all (uvicorn's
# config only covers its own three loggers and disables nothing else; the
# root logger keeps its WARNING default). The tests below exercise the real
# logging machinery with no pytest fixture in the path.


def test_log_config_routes_agent_legion_events_to_the_default_handler() -> None:
    """deploy/uvicorn-log-config.json must configure the `agent_legion`
    logger (or an ancestor of the event logger) with the stderr handler —
    this is the exact gap that dropped every Host-side event in production."""
    config = json.loads(LOG_CONFIG_PATH.read_text(encoding="utf-8"))
    loggers: dict = config.get("loggers", {})
    handlers: dict = config.get("handlers", {})
    assert "agent_legion" in loggers, (
        "the agent_legion logger needs an explicit entry: uvicorn's log "
        "config leaves the root logger at WARNING with no handler, so "
        "agent_legion.worker_events INFO/DEBUG lines are dropped in "
        "production deployments that pass --log-config"
    )
    entry = loggers["agent_legion"]
    assert entry.get("propagate") is False
    assert entry.get("level") == "INFO"
    configured = entry.get("handlers") or []
    assert configured, "the agent_legion entry must reference at least one handler"
    assert all(name in handlers for name in configured)


def test_worker_events_reach_a_real_handler_without_caplog(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end machinery check with pytest fully out of the logging path:
    configure logging from the shipped log-config (uvicorn formatter class
    swapped for a plain one — uvicorn need not be importable here), emit a
    transition event, and assert the JSON line physically arrives on the
    handler's stream."""
    config = json.loads(LOG_CONFIG_PATH.read_text(encoding="utf-8"))
    # Swap the uvicorn formatter factories for a stdlib one; the routing
    # (logger → handler → stream) is what this test pins.
    config["formatters"] = {"default": {"format": "%(message)s"}}
    for handler in config["handlers"].values():
        handler.pop("()", None)
        handler["formatter"] = "default"

    import io
    import sys

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    saved = {
        name: logging.getLogger(name) for name in ("agent_legion", "agent_legion.worker_events")
    }
    saved_state = [
        (logger, logger.handlers[:], logger.level, logger.propagate) for logger in saved.values()
    ]
    try:
        logging.config.dictConfig(config)
        emit_worker_event("worker.offline", {"worker_id": "vis-test"})
        assert any(
            isinstance(h, logging.StreamHandler) for h in logging.getLogger("agent_legion").handlers
        ), "dictConfig must have attached the stderr handler to agent_legion"
        emit_worker_event("worker.offline", {"worker_id": "vis-test-2"})
    finally:
        for logger, handlers, level, propagate in saved_state:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = False

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    assert payloads, "the worker.offline event must physically reach stderr"
    assert payloads[-1]["event"] == "worker.offline"
    assert payloads[-1]["worker_id"] == "vis-test-2"


def test_debug_rhythm_is_hidden_at_default_info_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """The INFO default must not open the DEBUG firehose: claim.granted /
    claim.empty stay silent with the shipped log-config (cost discipline —
    the level split is real, not just an integer on the record)."""
    config = json.loads(LOG_CONFIG_PATH.read_text(encoding="utf-8"))
    config["formatters"] = {"default": {"format": "%(message)s"}}
    for handler in config["handlers"].values():
        handler.pop("()", None)
        handler["formatter"] = "default"

    import io
    import sys

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    saved = {
        name: logging.getLogger(name) for name in ("agent_legion", "agent_legion.worker_events")
    }
    saved_state = [
        (logger, logger.handlers[:], logger.level, logger.propagate) for logger in saved.values()
    ]
    try:
        logging.config.dictConfig(config)
        emit_worker_event("claim.granted", {"worker_id": "w"})
        emit_worker_event("claim.empty", {"worker_id": "w"})
        emit_worker_event("claim.rejected", {"worker_id": "w", "reasons": {"capacity_full": 1}})
    finally:
        for logger, handlers, level, propagate in saved_state:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = False

    payloads = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    # DEBUG hidden, INFO (transition) visible.
    assert [p["event"] for p in payloads] == ["claim.rejected"]
