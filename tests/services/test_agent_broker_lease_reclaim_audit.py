"""#681: mass lease reclaim — Host-side evidence and audit lines.

- Reclaimed executions are requeued (attempt 1 <= requeue limit, never the
  "requeue limit exceeded" fail path) and the SAME Worker re-claims every
  one of them on its next poll: the Host side never strands them. The
  incident's hours-long idle came from the Worker side (crash auto-restart
  reset ``claim_enabled`` — tests/workers/test_supervisor_claim_resume.py).
- ``worker.lease_reclaim_burst``: one WARNING per Worker per sweep at/over
  the threshold, with the deferral (#566) and last-seen context.
- ``execution.result_rejected``: a refused terminal report is audited with
  the ownership reason and whether it carried output artifacts.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.lease_reclaim_audit import RECLAIM_BURST_THRESHOLD, reject_result
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers.agent_worker_api import broker, seed_request
from tests.postgres_support import TEST_DATABASE_URL

_TTL = 90
_AUDIT_LOGGER = "server.app.agent_broker.lease_reclaim_audit"


def _register(worker_id: str = "worker-1", capacity: int = 64) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        max_concurrency=capacity,
        labels={"arch": "arm64"},
    )


def _claim_all(
    job_db, count: int, worker_id: str = "worker-1"
) -> tuple[AgentExecutionBroker, list]:
    for index in range(count):
        seed_request(job_db, job_id=f"job-{index}", limit=1000)
    _register(worker_id)
    instance = broker(job_db.jobs_dir.parent, lease_ttl_seconds=_TTL)
    claims = [instance.claim(worker_id) for _ in range(count)]
    assert all(claim is not None for claim in claims)
    return instance, claims


def _silence(job_db, execution_ids: list[str], seconds: float) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id = any(%s)",
            (datetime.now(UTC) - timedelta(seconds=seconds), execution_ids),
        )


def _last_seen(job_db, worker_id: str, age_seconds: float) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update agent_workers set last_seen_at=%s where worker_id=%s",
            (datetime.now(UTC) - timedelta(seconds=age_seconds), worker_id),
        )


def _audit_events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict]:
    lines = [r for r in caplog.records if r.name == _AUDIT_LOGGER]
    assert all(r.levelno == logging.WARNING for r in lines)
    parsed = [json.loads(r.getMessage()) for r in lines]
    return [p for p in parsed if p["event"] == event]


def test_mass_reclaim_requeues_and_same_worker_reclaims_everything(job_db) -> None:
    """整批收回的最终状态 = queued（未命中 requeue limit），同一 worker 下一轮
    轮询即可全部领回——Host 侧不会让被收回的执行搁浅。"""
    count = RECLAIM_BURST_THRESHOLD + 2
    instance, claims = _claim_all(job_db, count)
    ids = [claim.execution_id for claim in claims]
    _silence(job_db, ids, _TTL + 10)
    _last_seen(job_db, "worker-1", 300)  # whole Worker silent: no deferral

    assert sorted(instance.sweep_expired_claims()) == sorted(ids)
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select state, attempt, outcome_json from agent_execution_requests"
            " where execution_id = any(%s)",
            (ids,),
        ).fetchall()
    assert {row["state"] for row in rows} == {"queued"}
    assert {int(row["attempt"]) for row in rows} == {1}
    assert {job_db.get_job_node(f"job-{i}", "generate")["status"] for i in range(count)} == {
        "pending"
    }

    reclaimed = [instance.claim("worker-1") for _ in range(count)]
    assert sorted(claim.execution_id for claim in reclaimed if claim) == sorted(ids)


def test_burst_line_names_worker_once_with_context(job_db, caplog) -> None:
    count = RECLAIM_BURST_THRESHOLD + 2
    instance, claims = _claim_all(job_db, count)
    ids = [claim.execution_id for claim in claims]
    _silence(job_db, ids, _TTL + 10)
    _last_seen(job_db, "worker-1", 300)

    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        instance.sweep_expired_claims()

    bursts = _audit_events(caplog, "worker.lease_reclaim_burst")
    assert len(bursts) == 1
    burst = bursts[0]
    assert burst["worker_id"] == "worker-1"
    assert burst["reclaimed"] == count
    assert burst["requeue_limit_exceeded"] == 0
    assert burst["deferred"] == 0
    assert burst["worker_last_seen_at"] is not None
    assert set(burst["sample_execution_ids"]) <= set(ids)
    assert burst["ts"]


def test_burst_counts_deferred_and_limit_exceeded(job_db, caplog) -> None:
    """worker 控制面新鲜：2×TTL 内的执行被延期（#566）计入 deferred；超过
    硬上限的照常收回；attempt 超过 requeue limit 的计入 requeue_limit_exceeded。"""
    count = RECLAIM_BURST_THRESHOLD + 3
    instance, claims = _claim_all(job_db, count)
    ids = [claim.execution_id for claim in claims]
    hard, soft = ids[: RECLAIM_BURST_THRESHOLD + 1], ids[RECLAIM_BURST_THRESHOLD + 1 :]
    _silence(job_db, hard, 2 * _TTL + 10)
    _silence(job_db, soft, _TTL + 10)
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set attempt=%s where execution_id=%s",
            (instance.requeue_limit + 1, hard[0]),
        )

    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        requeued = instance.sweep_expired_claims()

    assert sorted(requeued) == sorted(hard[1:])
    burst = _audit_events(caplog, "worker.lease_reclaim_burst")[0]
    assert burst["reclaimed"] == len(hard)
    assert burst["deferred"] == len(soft)
    assert burst["requeue_limit_exceeded"] == 1


def test_reclaim_below_threshold_emits_no_burst(job_db, caplog) -> None:
    instance, claims = _claim_all(job_db, 3)
    _silence(job_db, [claim.execution_id for claim in claims], _TTL + 10)
    _last_seen(job_db, "worker-1", 300)

    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        assert len(instance.sweep_expired_claims()) == 3

    assert _audit_events(caplog, "worker.lease_reclaim_burst") == []


def _record(**overrides) -> dict:
    return {"status": "completed", "exit_code": 0, "output_artifacts": {}, **overrides}


def test_rejected_result_reason_requeued_with_artifacts(job_db, caplog) -> None:
    instance, claims = _claim_all(job_db, 1)
    claim = claims[0]
    _silence(job_db, [claim.execution_id], _TTL + 10)
    _last_seen(job_db, "worker-1", 300)
    instance.sweep_expired_claims()
    record = _record(output_artifacts={"report": "sha256:" + "a" * 64})

    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        error = reject_result(
            TEST_DATABASE_URL,
            claim.execution_id,
            "worker-1",
            claim.lease_id,
            record,
            stage="precheck",
            detail="execution is not owned by this Worker",
            archive_bytes="2048",
        )

    assert error.status_code == 409
    (line,) = _audit_events(caplog, "execution.result_rejected")
    assert line["execution_id"] == claim.execution_id
    assert line["worker_id"] == "worker-1"
    assert line["reason"] == "requeued"
    assert line["stage"] == "precheck"
    assert line["carries_artifacts"] is True
    assert line["output_artifact_count"] == 1
    assert line["archive_bytes"] == 2048
    assert line["status"] == "completed" and line["exit_code"] == 0


def test_rejected_result_reason_reassigned_and_superseded(job_db, caplog) -> None:
    instance, claims = _claim_all(job_db, 1)
    claim = claims[0]
    _silence(job_db, [claim.execution_id], _TTL + 10)
    _last_seen(job_db, "worker-1", 300)
    instance.sweep_expired_claims()
    _register("worker-2")
    assert instance.claim("worker-2") is not None

    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        reject_result(
            TEST_DATABASE_URL, claim.execution_id, "worker-1", claim.lease_id, _record(),
            stage="commit", detail="x",
        )  # fmt: skip
        reject_result(
            TEST_DATABASE_URL, claim.execution_id, "worker-2", claim.lease_id, _record(),
            stage="commit", detail="x",
        )  # fmt: skip

    reasons = [line["reason"] for line in _audit_events(caplog, "execution.result_rejected")]
    assert reasons == ["reassigned", "superseded"]
    assert all(
        line["carries_artifacts"] is False and line["archive_bytes"] is None
        for line in _audit_events(caplog, "execution.result_rejected")
    )


def test_rejected_result_unknown_execution(job_db, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
        reject_result(
            TEST_DATABASE_URL, "exec-missing", "worker-1", "lease-x", _record(),
            stage="precheck", detail="x",
        )  # fmt: skip
    (line,) = _audit_events(caplog, "execution.result_rejected")
    assert line["reason"] == "missing"
