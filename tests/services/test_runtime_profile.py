"""Runtime profile: counters, sampling persistence and classifier (#359).

The L1 counters are pure in-process state (unit tier); the sampler and the
route run against the real schema (postgres tier — the profile table ships
in schema v72). The classifier tests pin the #351-review discrimination
table productized as rules: each verdict branch fires on its evidence
shape and only on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from server.app.services.runtime_profile import (
    RuntimeProfile,
    classify_bottleneck,
    persist_profile_sample,
    query_profile_series,
)
from server.app.services.runtime_profile.counters import RuntimeProfileCounters
from tests.helpers.profile_rows import bucket_matches
from tests.postgres_support import TEST_DATABASE_URL

_NOW = datetime(2026, 9, 2, 8, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# counters (unit tier)


def test_counters_snapshot_resets_deltas() -> None:
    counters = RuntimeProfileCounters()
    profile = RuntimeProfile()
    profile.counters = counters
    profile.note_run_intake(50)
    profile.note_pass(seconds=1.5, scan_seconds=1.0, slow=False)
    profile.note_claim(0.2, empty=True)
    profile.note_result(0.4)
    profile.note_execution_done()
    profile.note_execution_requeued(2)
    profile.note_enqueue_submitted()
    profile.note_enqueue_pool_skipped()

    snapshot = profile.counters.snapshot_and_reset()

    assert snapshot["intake_items"] == 50
    assert snapshot["pass_count"] == 1
    assert snapshot["claim_count"] == 1
    assert snapshot["claim_empty_count"] == 1
    assert snapshot["result_count"] == 1
    assert snapshot["execute_done"] == 1
    assert snapshot["execute_requeued"] == 2
    assert snapshot["enqueue_pool_skipped"] == 1
    # After the snapshot every counter restarts from zero.
    assert profile.counters.snapshot_and_reset()["claim_count"] == 0


def test_claim_timer_accumulates_exactly_once() -> None:
    """Codex P2 on #367: the timer must be pure — note_* owns accumulation.

    The old _Timed.stop also added to claim_seconds_total, so broker.claim
    double-counted every latency (timer + note_claim), inflating averages,
    the claim threshold and the DB-wait share alike.
    """
    profile = RuntimeProfile()
    elapsed = profile.claim_timer().stop()
    assert elapsed >= 0.0
    assert profile.counters.claim_seconds_total == 0.0  # pure timer: no add
    profile.note_claim(elapsed, empty=True)
    assert profile.counters.claim_seconds_total == elapsed  # exactly once


# ---------------------------------------------------------------------------
# classifier (unit tier — the discrimination table as rules)


def _sample(**overrides: object) -> dict:
    base = {
        "queued_depth": 0,
        "claim_count": 0,
        "claim_empty_count": 0,
        "claim_seconds_total": 0.0,
        "intake_items": 0,
        "pass_scan_seconds_max": 0.0,
        "pass_slow_count": 0,
        "enqueue_pool_skipped": 0,
        "enqueue_stock_gated": 0,
        "execute_active": 0,
        "db_pool_waiting": 0,
        "db_pool_wait_seconds_total": 0.0,
        "result_seconds_total": 0.0,
    }
    base.update(overrides)
    return base


def test_classifier_upstream_starvation_with_no_intake_blames_intake() -> None:
    verdict = classify_bottleneck(
        _sample(claim_count=100, claim_empty_count=95, intake_items=0),
        online_workers=4,
    )
    assert verdict["stage"] == "intake"
    assert "提交断流" in verdict["conclusion"]


def test_classifier_upstream_starvation_with_intake_blames_dag_bubble() -> None:
    verdict = classify_bottleneck(
        _sample(claim_count=100, claim_empty_count=95, intake_items=500),
        online_workers=4,
    )
    assert verdict["stage"] == "schedule"
    assert "DAG" in verdict["conclusion"]


def test_classifier_db_pool_contention_beats_stage_rules() -> None:
    verdict = classify_bottleneck(
        _sample(
            queued_depth=50,
            claim_count=100,
            claim_seconds_total=30.0,
            db_pool_waiting=8,
            db_pool_wait_seconds_total=9.0,
        ),
        online_workers=4,
    )
    assert verdict["stage"] == "db_pool"


def test_classifier_enqueue_pool_saturation() -> None:
    verdict = classify_bottleneck(
        _sample(queued_depth=50, enqueue_pool_skipped=12),
        online_workers=4,
    )
    assert verdict["stage"] == "enqueue"
    assert "agent_enqueue.workers" in verdict["conclusion"]


def test_classifier_slow_pass_blames_single_threaded_schedule() -> None:
    verdict = classify_bottleneck(
        _sample(queued_depth=50, pass_slow_count=3, pass_scan_seconds_max=9.5),
        online_workers=4,
    )
    assert verdict["stage"] == "schedule"
    assert "pass" in verdict["conclusion"]


def test_classifier_claim_serialization() -> None:
    verdict = classify_bottleneck(
        _sample(queued_depth=50, claim_count=40, claim_seconds_total=20.0),
        online_workers=4,
    )
    assert verdict["stage"] == "claim"
    assert "#351" in verdict["conclusion"]


def test_classifier_deep_queue_with_blocked_claims_names_claim_blockage() -> None:
    """#351 table's blocked row: deep queue, workers claiming, all skipped."""
    verdict = classify_bottleneck(
        _sample(queued_depth=50, claim_count=80, claim_empty_count=80),
        online_workers=4,
        queue_alert="blocked",
    )
    assert verdict["stage"] == "claim"
    assert "不可领取" in verdict["conclusion"]
    assert verdict["evidence"]["queue_alert"] == "blocked"


def test_classifier_no_signal_reports_none() -> None:
    verdict = classify_bottleneck(_sample(), online_workers=2)
    assert verdict["stage"] == "none"
    assert verdict["conclusion"] == ""


# ---------------------------------------------------------------------------
# sampling persistence + series read (postgres tier)


def test_persist_and_read_profile_sample() -> None:
    profile = RuntimeProfile()
    profile.note_run_intake(120)
    profile.note_claim(0.05, empty=True)
    profile.note_result(0.3)

    persist_profile_sample(
        TEST_DATABASE_URL,
        _NOW,
        profile,
        queued_depth=7,
        active_executions=3,
        enqueue_pending=11,
    )

    series = query_profile_series(TEST_DATABASE_URL, buckets=5)
    row = next(item for item in series if bucket_matches(item["bucket_start"], _NOW))
    assert row["intake_items"] == 120
    assert row["claim_count"] == 1
    assert row["claim_empty_count"] == 1
    assert row["execute_active"] == 3
    assert row["enqueue_pending"] == 11
    # The sampler reset the counters: persisting again with no traffic
    # overwrites the same bucket with zeros (upsert semantics).
    persist_profile_sample(
        TEST_DATABASE_URL,
        _NOW,
        profile,
        queued_depth=0,
        active_executions=0,
        enqueue_pending=0,
    )
    series = query_profile_series(TEST_DATABASE_URL, buckets=5)
    row = next(item for item in series if bucket_matches(item["bucket_start"], _NOW))
    assert row["intake_items"] == 0


def test_series_ordered_oldest_first() -> None:
    profile = RuntimeProfile()
    for minute in range(3):
        persist_profile_sample(
            TEST_DATABASE_URL,
            _NOW + timedelta(minutes=minute),
            profile,
            queued_depth=0,
            active_executions=0,
            enqueue_pending=0,
        )
    series = query_profile_series(TEST_DATABASE_URL, buckets=3)
    starts = [str(item["bucket_start"]) for item in series[-3:]]
    assert starts == sorted(starts)


# ---------------------------------------------------------------------------
# route (postgres tier via the app client)


def test_series_rollup_aggregates_wide_bins() -> None:
    from server.app.services.runtime_profile.sampling import _rollup
    from tests.helpers.profile_rows import bucket_matches

    profile = RuntimeProfile()
    base = _NOW.replace(second=0, microsecond=0)
    for minute in range(5):
        persist_profile_sample(
            TEST_DATABASE_URL,
            base + timedelta(minutes=minute),
            profile,
            queued_depth=0,
            active_executions=0,
            enqueue_pending=minute + 1,  # depth: max within the bin
        )
    rows = query_profile_series(TEST_DATABASE_URL, buckets=10)
    rolled = _rollup(rows, bin_seconds=300)
    assert len(rolled) == 1  # five minutes collapse into one 5-minute bin
    assert rolled[0]["enqueue_pending"] == 5  # max, not sum
    assert rolled[0]["pass_count"] == 0  # untouched counters stay 0
    assert bucket_matches(rolled[0]["bucket_start"], base)


def test_runtime_profile_route_serves_buckets_and_verdict(client: TestClient) -> None:
    response = client.get("/api/metrics/runtime-profile?window=6h")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["buckets"], list)
    verdict = payload["verdict"]
    assert verdict["stage"] in {
        "none",
        "intake",
        "schedule",
        "enqueue",
        "claim",
        "db_pool",
    }
    assert isinstance(verdict["evidence"], dict)
