"""Claim-stage timing instrumentation (issue #448 phase 1).

The worker claim loop is single-threaded serial — claim throughput equals
1 / (one claim round-trip) — so the claim transaction carries a stage timer
(worker_setup / scan / evaluate / writes; the transaction commit is
deliberately unmeasured, see claim_timing's docstring). These tests pin:

- the stage split reaches the log line (DEBUG per claim, WARNING past the
  threshold) with the claim verdict and attempt/skip counts;
- the stage timings fold into the #359 runtime profile (scan/evaluate/
  writes totals + maxes);
- empty claims count exactly once (the broker's note_claim is the single
  owner of claim_empty_count — the #461 review caught note_claim_stages
  double-counting them, doubling the classifier's empty_claim_ratio);
- a successful claim's writes segment carries the promote-write sequence
  (the #461 review's stage-attribution fix), not just touch_worker — and
  the #461终局复审 variant: the promote writes must land in writes, NOT
  leak back into evaluate through a second evaluate close on the success
  path (ClaimStageTimer.stage accumulates; the旧 >0 assertions could not
  catch that shape, so this pins writes > evaluate under an injected
  promote-write delay);
- the profile counters round-trip into the per-minute bucket row.
"""

from __future__ import annotations

import logging

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim_timing import (
    ClaimStageTimer,
    log_claim_stages,
    slow_claim_threshold_ms,
)
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.runtime_profile import RuntimeProfile
from server.app.services.runtime_profile.counters import RuntimeProfileCounters
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import insert_job_rows
from tests.postgres_support import TEST_DATABASE_URL

_WORKER_ID = "claim-stage-worker"
_WORKSPACE = "test-workspace"


def _seed_one(job_db, job_id: str, model: str = "test-model") -> AgentExecutionBroker:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog(_WORKSPACE, {"generator-v1": definition})
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key="generate",
        limit=20,
        workspace_id=_WORKSPACE,
        agent_id="generator-v1",
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id=_WORKSPACE,
            job_id=job_id,
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "execution": {"provider": "gateway", "model": model},
            },
        )
    )
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
        capabilities=["generate"],
        models=[{"provider": "gateway", "model": model}],
    )
    return broker


def test_claim_logs_stage_breakdown_on_success(job_db, caplog) -> None:
    broker = _seed_one(job_db, "stage-job-1")
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.claim_timing"):
        claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    debug_records = [
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert any("claim stages:" in message for message in debug_records)
    line = next(message for message in debug_records if "claim stages:" in message)
    assert "worker_setup=" in line
    assert "scan=" in line
    assert "evaluate=" in line
    assert "writes=" in line
    assert "total=" in line
    assert "claimed=True" in line
    assert "attempts=1" in line


def test_claim_logs_stage_breakdown_on_empty(job_db, caplog) -> None:
    # An empty claim (queue dry) still reports its stages — the worker_setup
    # and scan segments are exactly what a dry-queue poll costs.
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.claim_timing"):
        assert broker.claim(_WORKER_ID) is None
    debug_records = [
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    ]
    line = next(message for message in debug_records if "claim stages:" in message)
    assert "claimed=False" in line


def test_slow_claim_escalates_to_warning(job_db, caplog, monkeypatch) -> None:
    # Past AGENT_LEGION_SLOW_CLAIM_MS the same payload escalates to WARNING
    # (the slow-request middleware precedent: only slow requests pay the
    # always-on log line).
    broker = _seed_one(job_db, "stage-job-2")
    monkeypatch.setenv("AGENT_LEGION_SLOW_CLAIM_MS", "0")
    with caplog.at_level(logging.WARNING, logger="server.app.agent_broker.claim_timing"):
        claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("claim stages:" in message for message in warnings)


def test_stage_timings_fold_into_runtime_profile(job_db, monkeypatch) -> None:
    # The claim path must feed the #359 counters: claim_through the module
    # singleton (the profile.note_claim_stages call inside claim_timing).
    # Attribution is pinned by the writes-dominance test below; this one
    # pins wiring only (every stage lands in its gauge at all).
    broker = _seed_one(job_db, "stage-job-3")
    test_profile = RuntimeProfile()
    monkeypatch.setattr("server.app.services.runtime_profile.profile", test_profile, raising=False)
    claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    counters = test_profile.counters
    assert counters.claim_scan_seconds_total > 0.0
    assert counters.claim_scan_seconds_max > 0.0
    assert counters.claim_writes_seconds_total > 0.0
    assert counters.claim_evaluate_seconds_total > 0.0
    # note_claim (broker.claim's own finally) still runs on the module-level
    # singleton replaced above: claim_count must count this claim exactly once.
    assert counters.claim_count == 1
    assert counters.claim_empty_count == 0


def test_successful_claim_promote_writes_land_in_writes_not_evaluate(job_db, monkeypatch) -> None:
    # #461终局复审 P1 regression: ClaimStageTimer.stage ACCUMULATES
    # (stages[name] += delta), so a second evaluate close on the success path
    # (claim_windows's old "close evaluate before returning") folded the
    # whole promote write sequence back into evaluate — the writes-dominant
    # branch of the phase-2 triage was unreachable. Inject a controlled
    # delay into the promote write sequence (the node_runs INSERT, first
    # write past evaluate_candidate's stage boundary) and assert it lands in
    # writes, not evaluate: 200ms ≫ the un-delayed evaluate segment
    # (~sub-ms locally), so the constant is not load-bearing — the
    # inequality is, and it flips exactly when the redundant close returns.
    import time

    from server.app.db.connection import DatabaseConnection

    broker = _seed_one(job_db, "stage-job-4")
    real_db_execute = DatabaseConnection.execute

    def _slow_promote_execute(conn, sql, params=None):
        query = sql if isinstance(sql, str) else str(sql)
        if query.lstrip().startswith("insert into node_runs"):
            time.sleep(0.2)
        return real_db_execute(conn, sql, params)

    monkeypatch.setattr(DatabaseConnection, "execute", _slow_promote_execute)

    test_profile = RuntimeProfile()
    monkeypatch.setattr("server.app.services.runtime_profile.profile", test_profile, raising=False)
    claimed = broker.claim(_WORKER_ID)
    assert claimed is not None
    counters = test_profile.counters
    # The controlled delay landed in writes (≥200ms was injected into the
    # promote sequence) and evaluate stayed small: with the redundant close
    # present, evaluate would absorb the promote writes and exceed 0.2s.
    assert counters.claim_writes_seconds_total >= 0.2, counters.claim_writes_seconds_total
    assert counters.claim_evaluate_seconds_total < 0.2, counters.claim_evaluate_seconds_total


def test_empty_claim_counts_exactly_once(job_db, monkeypatch) -> None:
    # End-to-end empty claim (#461 P1): both note_claim (broker.claim's
    # finally) and note_claim_stages (claim.py's report helper) fire on the
    # same empty claim; only note_claim may bump claim_empty_count, or the
    # classifier's empty_claim_ratio doubles and drives false
    # intake/schedule/claim verdicts.
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    test_profile = RuntimeProfile()
    monkeypatch.setattr("server.app.services.runtime_profile.profile", test_profile, raising=False)
    assert broker.claim(_WORKER_ID) is None
    counters = test_profile.counters
    assert counters.claim_count == 1
    # The load-bearing assertion: exactly 1, not 2 (the double-count bug).
    assert counters.claim_empty_count == 1


# ---------------------------------------------------------------------------
# unit tier (pure timer objects)


def test_stage_timer_accumulates() -> None:
    # stage() is accumulate-close semantics: each call adds the elapsed time
    # since the PREVIOUS stage call into the named bucket — the property the
    # redundant-close bug abused (a repeated close re-adds the same span into
    # the old bucket).
    timer = ClaimStageTimer()
    timer.stage("worker_setup")
    timer.stage("scan")
    timer.stage("worker_setup")
    stages = timer.stages
    assert set(stages) == {"worker_setup", "scan"}
    assert all(value >= 0.0 for value in stages.values())


def test_log_claim_stages_formats_stages_in_fixed_order(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="server.app.agent_broker.claim_timing"):
        log_claim_stages(
            {"writes": 0.01, "scan": 0.2, "worker_setup": 0.005, "evaluate": 0.03},
            worker_id="w",
            claimed=True,
            attempts=2,
            skipped=4,
        )
    message = caplog.records[0].getMessage()
    scan_at = message.index("scan=")
    evaluate_at = message.index("evaluate=")
    writes_at = message.index("writes=")
    worker_setup_at = message.index("worker_setup=")
    assert worker_setup_at < scan_at < evaluate_at < writes_at


def test_slow_claim_threshold_ignores_malformed_env(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LEGION_SLOW_CLAIM_MS", raising=False)
    assert slow_claim_threshold_ms() == 5000.0
    monkeypatch.setenv("AGENT_LEGION_SLOW_CLAIM_MS", "not-a-number")
    assert slow_claim_threshold_ms() == 5000.0
    monkeypatch.setenv("AGENT_LEGION_SLOW_CLAIM_MS", "250")
    assert slow_claim_threshold_ms() == 250.0


def test_counters_claim_stages_do_not_count_empty_claims() -> None:
    # #461 P1: note_claim_stages only folds timings — claim counting
    # (claim_count / claim_empty_count) is note_claim's exclusive job on the
    # broker's claim lifecycle; folding stages for an empty claim must leave
    # the empty counter untouched.
    profile = RuntimeProfile()
    profile.note_claim_stages({"scan": 0.1, "evaluate": 0.05, "writes": 0.02})
    profile.note_claim_stages({"scan": 0.3, "evaluate": 0.01, "writes": 0.0})
    counters = profile.counters
    assert counters.claim_scan_seconds_total == pytest.approx(0.4)
    assert counters.claim_scan_seconds_max == pytest.approx(0.3)
    assert counters.claim_evaluate_seconds_total == pytest.approx(0.06)
    assert counters.claim_evaluate_seconds_max == pytest.approx(0.05)
    assert counters.claim_writes_seconds_total == pytest.approx(0.02)
    assert counters.claim_empty_count == 0
    assert counters.claim_count == 0


def test_counters_snapshot_carries_stage_deltas() -> None:
    profile = RuntimeProfile()
    profile.note_claim_stages({"scan": 0.2, "evaluate": 0.1, "writes": 0.05})
    snapshot = profile.counters.snapshot_and_reset()
    assert snapshot["claim_scan_seconds_total"] == 0.2
    assert snapshot["claim_scan_seconds_max"] == 0.2
    assert snapshot["claim_evaluate_seconds_total"] == 0.1
    assert snapshot["claim_evaluate_seconds_max"] == 0.1
    assert snapshot["claim_writes_seconds_total"] == 0.05
    assert snapshot["claim_writes_seconds_max"] == 0.05
    # Post-snapshot reset (RuntimeProfileCounters contract).
    fresh = RuntimeProfileCounters()
    assert profile.counters.claim_scan_seconds_total == fresh.claim_scan_seconds_total
