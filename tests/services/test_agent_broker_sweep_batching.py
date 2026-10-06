"""#957: sweep_expired_claims 批次上限与批读——行为与逐行版一致。

- 每周期至多 ``SWEEP_BATCH_LIMIT`` 行，最老心跳优先，剩余下一周期接着收。
- 代次与 lease 状态按批各读一次（不再逐行 2 次 select），lease 删除与
  node_run 落库合为一条；收回后的终态（queued / node pending / node_run
  failed / lease 删除）与改动前相同。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_broker import sweepers
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers.agent_worker_api import broker, seed_request
from tests.postgres_support import TEST_DATABASE_URL

_TTL = 90


class _CountingConn:
    """Records every SQL text a sweep transaction executes."""

    def __init__(self, conn: Any, log: list[str]) -> None:
        self._conn = conn
        self._log = log

    def execute(self, sql: Any, *args: Any, **kwargs: Any) -> Any:
        self._log.append(str(sql))
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _count_statements(monkeypatch) -> list[str]:
    log: list[str] = []
    real = sweepers.write_transaction

    @contextmanager
    def counting(dsn: str):
        with real(dsn) as conn:
            yield _CountingConn(conn, log)

    monkeypatch.setattr(sweepers, "write_transaction", counting)
    return log


def _claim_all(job_db, count: int):
    for index in range(count):
        seed_request(job_db, job_id=f"job-{index}", limit=1000)
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker-1",
        runtimes=["pi"],
        max_concurrency=64,
        labels={"arch": "arm64"},
    )
    instance = broker(job_db.jobs_dir.parent, lease_ttl_seconds=_TTL)
    claims = [instance.claim("worker-1") for _ in range(count)]
    assert all(claim is not None for claim in claims)
    with job_db.connect() as conn:
        # Whole Worker silent: no #566 deferral.
        conn.execute(
            "update agent_workers set last_seen_at=%s where worker_id='worker-1'",
            (datetime.now(UTC) - timedelta(seconds=300),),
        )
    return instance, claims


def _age(job_db, execution_id: str, seconds: float) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=seconds), execution_id),
        )


def test_sweep_caps_batch_oldest_first_and_drains_next_cycle(job_db, monkeypatch) -> None:
    instance, claims = _claim_all(job_db, 3)
    ids = [claim.execution_id for claim in claims]
    for offset, execution_id in enumerate(ids):
        _age(job_db, execution_id, _TTL + 100 - offset * 10)  # ids[0] oldest
    monkeypatch.setattr(sweepers, "SWEEP_BATCH_LIMIT", 2)

    assert sorted(instance.sweep_expired_claims()) == sorted(ids[:2])
    with job_db._connect_read() as conn:
        left = conn.execute(
            "select state from agent_execution_requests where execution_id=%s", (ids[2],)
        ).fetchone()
    assert left["state"] == "claimed"  # left for the next cycle, untouched

    assert instance.sweep_expired_claims() == [ids[2]]
    assert instance.sweep_expired_claims() == []


def test_sweep_batch_reads_once_and_keeps_requeue_outcome(job_db, monkeypatch) -> None:
    instance, claims = _claim_all(job_db, 4)
    ids = [claim.execution_id for claim in claims]
    for execution_id in ids:
        _age(job_db, execution_id, _TTL + 10)
    with job_db._connect_read() as conn:
        before = {
            str(r["execution_id"]): (str(r["lease_id"]), int(r["node_run_id"]))
            for r in conn.execute(
                "select execution_id, lease_id, node_run_id from agent_execution_requests"
                " where execution_id = any(%s)",
                (ids,),
            ).fetchall()
        }
    log = _count_statements(monkeypatch)

    assert sorted(instance.sweep_expired_claims()) == sorted(ids)

    # One batched read per table, never one per row.
    assert sum("from jobs where id = any" in sql for sql in log) == 1
    assert sum("from executor_leases where id = any" in sql for sql in log) == 1
    assert not any("select execution_generation from jobs where id=%s" in sql for sql in log)
    assert not any("select status from executor_leases where id=%s" in sql for sql in log)
    # One job lock per job, and the lease delete + node_run fail share one statement.
    assert sum("pg_advisory_xact_lock" in sql for sql in log) == len(ids)
    assert sum("delete from executor_leases" in sql for sql in log) == len(ids)
    assert not any(sql.startswith("update node_runs") for sql in log)

    with job_db._connect_read() as conn:
        requests = conn.execute(
            "select execution_id, state, worker_id, lease_id from agent_execution_requests"
            " where execution_id = any(%s)",
            (ids,),
        ).fetchall()
        lease_ids = [lease for lease, _ in before.values()]
        run_ids = [run for _, run in before.values()]
        leases = conn.execute(
            "select count(*) as cnt from executor_leases where id = any(%s)", (lease_ids,)
        ).fetchone()
        runs = conn.execute(
            "select status, error_message from node_runs where id = any(%s)", (run_ids,)
        ).fetchall()
        nodes = conn.execute(
            "select status from job_nodes where job_id = any(%s)",
            ([f"job-{i}" for i in range(4)],),
        ).fetchall()
    assert all(r["state"] == "queued" and r["worker_id"] is None for r in requests)
    assert all(r["lease_id"] is None for r in requests)
    assert int(leases["cnt"]) == 0
    assert all(r["status"] == "failed" for r in runs)
    assert all(r["error_message"] == "Agent Worker heartbeat expired" for r in runs)
    assert all(r["status"] == "pending" for r in nodes)


def test_orphan_claims_beyond_cap_do_not_block_real_expiry(job_db, monkeypatch) -> None:
    """lease 为空 / lease 行已不存在的孤儿行不进批：数量超过上限也不挡住真实过期行。"""
    instance, claims = _claim_all(job_db, 4)
    ids = [claim.execution_id for claim in claims]
    orphans, real = ids[:3], ids[3]
    for offset, execution_id in enumerate(ids):
        _age(job_db, execution_id, _TTL + 100 - offset * 10)  # orphans oldest
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set lease_id=null where execution_id = any(%s)",
            (orphans[:2],),
        )
        conn.execute(
            "delete from executor_leases where id ="
            " (select lease_id from agent_execution_requests where execution_id=%s)",
            (orphans[2],),
        )
    monkeypatch.setattr(sweepers, "SWEEP_BATCH_LIMIT", 2)

    assert instance.sweep_expired_claims() == [real]
    with job_db._connect_read() as conn:
        states = conn.execute(
            "select state from agent_execution_requests where execution_id = any(%s)", (orphans,)
        ).fetchall()
    assert [r["state"] for r in states] == ["claimed"] * 3  # base skip semantics: untouched
