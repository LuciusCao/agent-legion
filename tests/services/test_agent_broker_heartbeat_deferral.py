"""#566: sweep_expired_claims defers expired claims whose Worker control
plane is still fresh (heartbeat-thread starvation is not Worker death),
bounded by a hard cutoff at 2×TTL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.claim_scan import AgentClaim
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.runtime_profile import profile
from tests.helpers.agent_worker_api import broker, seed_request
from tests.postgres_support import TEST_DATABASE_URL

_TTL = 90


def _broker(job_db) -> AgentExecutionBroker:
    return broker(job_db.jobs_dir.parent, lease_ttl_seconds=_TTL)


def _register_fresh_worker(job_db, worker_id: str = "worker-1") -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id=worker_id,
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def _set_worker_last_seen(job_db, worker_id: str, age_seconds: float) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update agent_workers set last_seen_at=%s where worker_id=%s",
            (datetime.now(UTC) - timedelta(seconds=age_seconds), worker_id),
        )


def _silence_heartbeat(job_db, execution_id: str, silence_seconds: float) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=silence_seconds), execution_id),
        )


def _claim(job_db) -> tuple[AgentExecutionBroker, AgentClaim]:
    _register_fresh_worker(job_db)
    broker = _broker(job_db)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    return broker, claimed


def test_fresh_worker_heartbeat_silence_within_grace_is_deferred(
    job_db, caplog, monkeypatch
) -> None:
    """Worker 控制面新鲜 + 静默在 2×TTL 内：不删租约、不重排队、不计数。"""
    seed_request(job_db, job_id="job-1")
    broker_instance, claimed = _claim(job_db)
    _silence_heartbeat(job_db, claimed.execution_id, _TTL + 10)
    requeued_calls: list[int] = []
    done_calls: list[int] = []
    monkeypatch.setattr(profile, "note_execution_requeued", lambda n: requeued_calls.append(n))
    monkeypatch.setattr(profile, "note_execution_done", lambda n: done_calls.append(n))

    with caplog.at_level(logging.WARNING, logger="server.app.agent_broker.heartbeat_deferral"):
        assert broker_instance.sweep_expired_claims() == []

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state, worker_id, lease_id from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
        lease = conn.execute(
            "select status from executor_leases where id=%s", (claimed.lease_id,)
        ).fetchone()
    assert row["state"] == "claimed"
    assert row["worker_id"] == "worker-1"
    assert lease is not None and lease["status"] == "active"
    assert job_db.get_job_node("job-1", "generate")["status"] == "running"
    assert requeued_calls == [] and done_calls == []
    warnings = [r for r in caplog.records if "deferring expired agent lease" in r.message]
    assert len(warnings) == 1
    assert claimed.execution_id in warnings[0].message
    assert "worker-1" in warnings[0].message

    # A second sweep in the same TTL bucket stays silent (no per-sweep flood).
    caplog.clear()
    assert broker_instance.sweep_expired_claims() == []
    assert not [r for r in caplog.records if "deferring expired agent lease" in r.message]

    # The lease still lives, so a recovered heartbeat renews it.
    assert broker_instance.heartbeat(claimed.execution_id, "worker-1", claimed.lease_id) is True


def test_fresh_worker_heartbeat_silence_beyond_grace_expires(job_db) -> None:
    """硬兜底：静默超过 2×TTL 照常过期重排（attempt 线程真死的场景）。"""
    seed_request(job_db, job_id="job-1")
    broker_instance, claimed = _claim(job_db)
    _silence_heartbeat(job_db, claimed.execution_id, 2 * _TTL + 10)

    assert broker_instance.sweep_expired_claims() == [claimed.execution_id]

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state, worker_id, lease_id from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
    assert row["state"] == "queued"
    assert row["worker_id"] is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"


def test_stale_worker_expires_as_before(job_db) -> None:
    """控制面不新鲜（worker 真离线）：行为完全不变，照常过期重排。"""
    seed_request(job_db, job_id="job-1")
    broker_instance, claimed = _claim(job_db)
    _silence_heartbeat(job_db, claimed.execution_id, _TTL + 10)
    _set_worker_last_seen(job_db, "worker-1", age_seconds=300)

    assert broker_instance.sweep_expired_claims() == [claimed.execution_id]
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"


def test_closed_lease_close_path_not_deferred(job_db) -> None:
    """lease 已被结果路径释放的关闭路径不受新分支影响：照样关 done。"""
    seed_request(job_db, job_id="job-1")
    broker_instance, claimed = _claim(job_db)
    _silence_heartbeat(job_db, claimed.execution_id, _TTL + 10)
    with job_db.connect() as conn:
        conn.execute(
            "update executor_leases set status='released' where id=%s",
            (claimed.lease_id,),
        )

    assert broker_instance.sweep_expired_claims() == []

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
    assert row["state"] == "done"
