"""Result-stage timing instrumentation (issue #521).

A completion wave's commits saturate the single-process control plane;
the stage timer splits one commit into unpack / artifacts_verify /
validate / artifacts_upload / lease_write / events / mark_done segments
(the #448 claim-split pattern applied to the result path). These tests
pin the timing primitives in isolation: stage accumulation, the log
line's level switching, the profile fold (exactly-once, unknown keys
ignored, result-wide counters untouched), and the 409-path reporting
discipline (a rejected commit still reports what it measured).
"""

from __future__ import annotations

import logging

from server.app.agent_broker.result_timing import (
    ResultStageTimer,
    log_result_stages,
    report_result_stages,
    slow_result_threshold_ms,
)
from server.app.services.runtime_profile import RuntimeProfile


def test_stage_timer_accumulates_segments() -> None:
    timer = ResultStageTimer()
    timer.stage("unpack")
    timer.stage("artifacts_verify")
    timer.stage("lease_write")
    assert set(timer.stages) == {"unpack", "artifacts_verify", "lease_write"}
    # Monotonic non-negative segments on a quiet machine.
    assert all(seconds >= 0 for seconds in timer.stages.values())


def test_repeated_stage_name_accumulates_not_replaces() -> None:
    # ResultStageTimer.stage ADDS elapsed time since the previous call —
    # the same contract the claim timer keeps when one stage closes twice.
    timer = ResultStageTimer()
    timer.stage("events")
    timer.stage("events")
    assert len(timer.stages) == 1
    assert timer.stages["events"] >= 0


def test_log_result_stages_debug_below_threshold(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.result_timing"):
        log_result_stages(
            {"unpack": 0.01, "lease_write": 0.02},
            execution_id="exec-1",
            worker_id="w-1",
            committed=True,
        )
    record = caplog.records[-1]
    assert record.levelno == logging.DEBUG
    assert "unpack=" in record.getMessage()
    assert "exec-1" in record.getMessage()
    assert "committed=True" in record.getMessage()


def test_log_result_stages_warns_past_threshold(monkeypatch, caplog) -> None:
    monkeypatch.setenv("AGENT_LEGION_SLOW_RESULT_MS", "100")
    assert slow_result_threshold_ms() == 100.0
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.result_timing"):
        log_result_stages({"events": 0.2}, execution_id="exec-1", worker_id="w-1", committed=False)
    assert caplog.records[-1].levelno == logging.WARNING


def test_log_result_stages_skips_empty_stages(caplog) -> None:
    # A 409 before any stage marker (e.g. the ownership rejection) logs
    # nothing — there is no breakdown to report.
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.result_timing"):
        log_result_stages({}, execution_id="exec-1", worker_id="w-1", committed=False)
    assert caplog.records == []


def test_malformed_threshold_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_SLOW_RESULT_MS", "not-a-number")
    assert slow_result_threshold_ms() == 15000.0


def test_note_result_stages_folds_known_keys_only() -> None:
    profile = RuntimeProfile()
    profile.note_result_stages(
        {"unpack": 0.1, "artifacts_verify": 0.5, "spool": 9.0, "commit": 1.0}
    )
    deltas = profile.counters.snapshot_and_reset()
    assert deltas["result_unpack_seconds_total"] == 0.1
    assert deltas["result_unpack_seconds_max"] == 0.1
    assert deltas["result_artifacts_verify_seconds_total"] == 0.5
    assert deltas["result_artifacts_verify_seconds_max"] == 0.5
    # Unknown keys fold into nothing; the result-wide gauges are
    # note_result's exclusive property (#461 lesson applied at birth).
    assert deltas["result_events_seconds_total"] == 0.0
    assert deltas["result_count"] == 0
    assert deltas["result_seconds_total"] == 0.0


def test_note_result_stages_accumulates_across_commits() -> None:
    profile = RuntimeProfile()
    profile.note_result_stages({"events": 1.0})
    profile.note_result_stages({"events": 3.0})
    deltas = profile.counters.snapshot_and_reset()
    assert deltas["result_events_seconds_total"] == 4.0
    assert deltas["result_events_seconds_max"] == 3.0
    # Snapshot-and-reset zeroed the gauges.
    assert profile.counters.snapshot_and_reset()["result_events_seconds_total"] == 0.0


def test_report_result_stages_reports_rejected_attempts(caplog) -> None:
    # The 409 discipline: a rejected commit (committed=False) still logs
    # and folds its partial stage measurements — attempt-level, like the
    # claim timer inside the transaction.
    timer = ResultStageTimer()
    timer.stage("unpack")
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.result_timing"):
        report_result_stages(timer, execution_id="exec-9", worker_id="w-1", committed=False)
    assert "committed=False" in caplog.records[-1].getMessage()
