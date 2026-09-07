"""Route tests for the per-Worker batch heartbeat endpoint (#352).

``POST /api/agent-executions/heartbeats`` renews every listed lease of the
authenticated Worker in one write transaction: a batch fully renewed, a
partial batch (unknown/expired ids answered per item, never 5xx), the empty
batch, auth (worker token only, and only this Worker's leases renew — a
foreign Worker's execution is lost), the batch size cap, and the cancel body
for code executions. The single heartbeat endpoint's behavior is pinned by
tests/routes/test_agent_workers.py and stays untouched here.

#499: the lost items also emit one ``execution.heartbeat_rejected`` each,
with the same reason literals the single path uses (not_owned for the
row-miss failure point, lease_not_active for the released-lease point),
emitted after the batch transaction commits.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers.agent_worker_api import (
    authenticate_admin,
    claim,
    issue_scoped_token,
    make_app,
    register,
    seed_request,
)

_BATCH_URL = "/api/agent-executions/heartbeats"


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Live view of the worker-events logger as parsed JSON payloads."""
    caplog.set_level(logging.DEBUG, logger="agent_legion.worker_events")
    captured: list[dict] = []

    class _View:
        def __getitem__(self, index: int) -> dict:
            return self._live[index]

        def __iter__(self):
            return iter(self._live)

        def __len__(self) -> int:
            return len(self._live)

        @property
        def _live(self) -> list[dict]:
            fresh = [
                json.loads(record.getMessage())
                for record in caplog.records
                if record.name == "agent_legion.worker_events"
            ]
            captured.extend(record for record in fresh if record not in captured)
            return captured

    return _View()


def _heartbeat_events(events) -> list[dict]:
    return [event for event in events if event["event"] == "execution.heartbeat_rejected"]


def _register_second_worker(client: TestClient) -> str:
    credential = issue_scoped_token(client)
    response = client.post(
        "/api/agent-workers/register",
        headers={"X-Agent-Worker-Register-Token": credential},
        json={
            "worker_id": "other-worker",
            "name": "Other",
            "runtimes": ["pi"],
            "models": [{"provider": "gateway", "model": "test-model"}],
            "max_concurrency": 10,
            "labels": {"arch": "arm64"},
            "protocol_version": 1,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["worker_token"])


def _heartbeat_ok(client: TestClient, token: str, items: list[dict]) -> dict:
    response = client.post(
        _BATCH_URL,
        headers={"X-Agent-Worker-Token": token},
        json={"executions": items},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_batch_heartbeat_renews_all_owned_executions(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for index in range(3):
        seed_request(app.state.job_db, job_id=f"job-{index}", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        claimed = [claim(client, token) for _ in range(3)]
        items = [
            {"execution_id": row["execution_id"], "lease_id": row["lease_id"]} for row in claimed
        ]

        outcome = _heartbeat_ok(client, token, items)

    assert sorted(outcome["renewed"]) == sorted(row["execution_id"] for row in claimed)
    assert outcome["lost"] == []
    assert outcome["cancelled_execution_ids"] == []
    with app.state.job_db._connect_read() as conn:
        rows = conn.execute(
            "select execution_id, heartbeat_at from agent_execution_requests where state='claimed'"
        ).fetchall()
    assert len(rows) == 3


def test_batch_heartbeat_reports_lost_items_without_failing_the_batch(
    tmp_path: Path,
    events,
) -> None:
    """未知 id / 过期 lease / 错误 lease：逐项进 lost，其余照常续期，不 5xx。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)
    seed_request(app.state.job_db, job_id="job-2", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        first = claim(client, token)
        second = claim(client, token)
        items = [
            # Unknown execution id.
            {"execution_id": "exec-unknown", "lease_id": "lease-x"},
            # Right execution, wrong lease (stale attempt after a requeue).
            {"execution_id": first["execution_id"], "lease_id": "not-the-lease"},
            # Healthy sibling: must still renew.
            {"execution_id": second["execution_id"], "lease_id": second["lease_id"]},
        ]

        outcome = _heartbeat_ok(client, token, items)

    assert outcome["renewed"] == [second["execution_id"]]
    assert sorted(outcome["lost"]) == sorted(["exec-unknown", first["execution_id"]])
    # The renewed sibling's request row carries a fresh heartbeat timestamp.
    with app.state.job_db._connect_read() as conn:
        rows = {
            str(row["execution_id"]): row["heartbeat_at"]
            for row in conn.execute(
                "select execution_id, heartbeat_at from agent_execution_requests"
                " where state='claimed'"
            ).fetchall()
        }
    assert rows[second["execution_id"]] is not None


def test_batch_heartbeat_accepts_empty_batch(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        outcome = _heartbeat_ok(client, token, [])

    assert outcome == {"renewed": [], "lost": [], "cancelled_execution_ids": []}


def test_batch_heartbeat_requires_worker_token(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with TestClient(app) as client:
        anonymous = client.post(_BATCH_URL, json={"executions": []})
        assert anonymous.status_code == 401
        invalid = client.post(
            _BATCH_URL,
            headers={"X-Agent-Worker-Token": "not-a-token"},
            json={"executions": []},
        )
        assert invalid.status_code == 401


def test_batch_heartbeat_never_renews_another_workers_execution(tmp_path: Path) -> None:
    """防跨 worker 误续：另一台机器的 execution 对本 Worker 是 lost，且
    原持有者的租约不受影响——其随后仍能正常续期（同一 lease 原样 200）。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        owner_token = register(client)["worker_token"]
        claimed = claim(client, owner_token)
        intruder_token = _register_second_worker(client)

        intrusion = _heartbeat_ok(
            client,
            intruder_token,
            [{"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]}],
        )
        # The owner renews right after the intrusion attempt: the intruder's
        # lost verdict must not have touched the owner's lease/heartbeat.
        owner_renewal = _heartbeat_ok(
            client,
            owner_token,
            [{"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]}],
        )
        # And the owner's single-beat channel still works for the same lease.
        single = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/heartbeat",
            headers={
                "X-Agent-Worker-Token": owner_token,
                "X-Agent-Lease-Id": claimed["lease_id"],
            },
        )
        assert single.status_code == 204

    assert intrusion["renewed"] == []
    assert intrusion["lost"] == [claimed["execution_id"]]
    assert owner_renewal["renewed"] == [claimed["execution_id"]]
    assert owner_renewal["lost"] == []


def test_batch_heartbeat_rejects_oversized_batch(tmp_path: Path) -> None:
    from server.app.agent_broker.heartbeat_batch import MAX_BATCH_HEARTBEATS

    app = make_app(tmp_path)
    items = [{"execution_id": f"exec-{index}", "lease_id": "lease"} for index in range(300)]
    assert len(items) > MAX_BATCH_HEARTBEATS

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        response = client.post(
            _BATCH_URL,
            headers={"X-Agent-Worker-Token": token},
            json={"executions": items},
        )

    assert response.status_code == 422, response.text


def test_batch_heartbeat_returns_cancel_body_for_code_executions(tmp_path: Path) -> None:
    """批量心跳沿用单条心跳的 v2 取消语义：body 携带本 Worker 的 code 取消列表。"""
    from tests.helpers.agent_worker_api import enqueue_code, insert_code_job_rows

    app = make_app(tmp_path)
    insert_code_job_rows(app.state.job_db, job_id="job-code-1")
    execution_id = enqueue_code(app.state.agent_broker, job_id="job-code-1")

    with TestClient(app) as client:
        authenticate_admin(client)
        credential = issue_scoped_token(client)
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": credential},
            json={
                "worker_id": "code-worker",
                "runtimes": ["pi", "velites"],
                "max_concurrency": 4,
                "max_code_concurrency": 2,
                "protocol_version": 2,
            },
        )
        assert response.status_code == 201, response.text
        token = str(response.json()["worker_token"])
        claimed = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "code-worker", "max_code_concurrency": 2},
        )
        assert claimed.status_code == 200, claimed.text
        lease_id = claimed.json()["lease_id"]

        idle = _heartbeat_ok(client, token, [])
        assert idle["cancelled_execution_ids"] == []

        from server.app.db.transaction import write_transaction

        with write_transaction(app.state.job_db.dsn_identity) as conn:
            conn.execute("update jobs set execution_paused=1 where id='job-code-1'")
        cancelled = _heartbeat_ok(
            client, token, [{"execution_id": execution_id, "lease_id": lease_id}]
        )

    assert cancelled["cancelled_execution_ids"] == [execution_id]


def test_batch_heartbeat_deduplicates_execution_ids(tmp_path: Path) -> None:
    """同一 execution 出现两次（同一 lease）：折叠为一次续期，renewed 不重复。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        claimed = claim(client, token)
        outcome = _heartbeat_ok(
            client,
            token,
            [
                {"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]},
                {"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]},
            ],
        )

    assert outcome["renewed"] == [claimed["execution_id"]]
    assert outcome["lost"] == []


def test_single_heartbeat_endpoint_still_works_alongside_batch(tmp_path: Path) -> None:
    """混合舰队钉子：旧 Worker 的单条端点行为不变（204/v2 body），新旧通道
    在同一 Host 上并存。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client, protocol_version=1)["worker_token"]
        claimed = claim(client, token)
        single = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/heartbeat",
            headers={"X-Agent-Worker-Token": token, "X-Agent-Lease-Id": claimed["lease_id"]},
        )
        assert single.status_code == 204

        batch = _heartbeat_ok(
            client,
            token,
            [{"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]}],
        )
        assert batch["renewed"] == [claimed["execution_id"]]


# ---------------------------------------------------------------------------
# #499: lost items must surface in the event stream. The single path emits
# execution.heartbeat_rejected on both refusal points; the batch path (v5, the
# forward default) must do the same per lost item, with the SAME reason
# literals — and only after the batch transaction commits (#498 discipline).


def test_batch_heartbeat_lost_items_emit_rejected_events_per_failure_point(
    tmp_path: Path, events
) -> None:
    """两个失败点分开：row 缺失（未知 id / 错误 lease / 他人 execution）→
    not_owned；行在但 lease 已释放 → lease_not_active。每项一条事件，reason
    正确。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)
    seed_request(app.state.job_db, job_id="job-2", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        first = claim(client, token)
        second = claim(client, token)
        # Failure point 2: release the lease (a concurrent finish/expiry), so
        # the row IS this Worker's under this lease but the lease is dead.
        from server.app.db.transaction import write_transaction

        with write_transaction(app.state.job_db.dsn_identity) as conn:
            conn.execute(
                "update executor_leases set status='released' where id=%s", (second["lease_id"],)
            )
        items = [
            # Failure point 1: unknown execution id (row is None).
            {"execution_id": "exec-unknown", "lease_id": "lease-x"},
            # Failure point 1: right execution, wrong lease.
            {"execution_id": first["execution_id"], "lease_id": "not-the-lease"},
            # Failure point 2: right execution + lease, lease released.
            {"execution_id": second["execution_id"], "lease_id": second["lease_id"]},
        ]

        outcome = _heartbeat_ok(client, token, items)

    assert outcome["renewed"] == []
    assert sorted(outcome["lost"]) == sorted(
        ["exec-unknown", first["execution_id"], second["execution_id"]]
    )
    rejected = _heartbeat_events(events)
    assert len(rejected) == 3, "one event per lost item, none for the (empty) renewed set"
    by_execution = {event["execution_id"]: event for event in rejected}
    assert by_execution["exec-unknown"]["reason"] == "not_owned"
    assert by_execution["exec-unknown"]["worker_id"] == "home-mini"
    assert by_execution[first["execution_id"]]["reason"] == "not_owned"
    assert by_execution[second["execution_id"]]["reason"] == "lease_not_active"


def test_batch_heartbeat_fully_renewed_batch_emits_no_rejected_events(
    tmp_path: Path, events
) -> None:
    """全续期：无 lost 项即无事件（健康心跳不是事件流噪音）。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        claimed = claim(client, token)
        outcome = _heartbeat_ok(
            client,
            token,
            [{"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]}],
        )

    assert outcome["renewed"] == [claimed["execution_id"]]
    assert outcome["lost"] == []
    assert _heartbeat_events(events) == []


def test_batch_heartbeat_no_events_when_transaction_fails(
    tmp_path: Path, events, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#498 纪律统一适用：批量事务 commit 失败（连接在 COMMIT 点丢失）时，
    已决定的 lost verdict 不得进入事件流——回滚的事务从未发生。

    纯 broker 层（不建 app/不启后台线程——采样线程的 commit 会吃掉一次性
    注入臂）；一个未知项的 lost verdict 在事务内已决定，commit 注入失败后
    不得出现在事件流。"""
    from psycopg import OperationalError

    import server.app.db.transaction as transaction_module
    from server.app.agent_broker import AgentExecutionBroker
    from server.app.agent_broker.heartbeat_batch import batch_heartbeat
    from tests.postgres_support import TEST_DATABASE_URL

    real_connect = transaction_module.connect_database
    state = {"armed": False}

    def _connect(dsn):
        conn = real_connect(dsn)
        real_commit = conn.commit

        def _commit() -> None:
            if state["armed"]:
                raise OperationalError("commit lost (scripted)")
            real_commit()

        conn.commit = _commit  # type: ignore[method-assign]
        return conn

    monkeypatch.setattr(transaction_module, "connect_database", _connect)

    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=tmp_path)
    state["armed"] = True
    # The lost verdict (exec-unknown → not_owned) is decided inside the
    # transaction; the commit then fails, so it must not surface.
    with pytest.raises(OperationalError):
        batch_heartbeat(
            broker,
            "no-such-worker",
            [{"execution_id": "exec-unknown", "lease_id": "lease-x"}],
        )

    assert _heartbeat_events(events) == []


def test_single_heartbeat_rejection_reasons_match_batch_literals(tmp_path: Path, events) -> None:
    """单条与批量共享 reason 字面量（worker_events.HEARTBEAT_*）：两条路径的
    not_owned 事件逐字段一致，防漂移（issue #499 的 helper 建议）。"""
    from server.app.agent_broker.worker_events import (
        HEARTBEAT_LEASE_NOT_ACTIVE,
        HEARTBEAT_NOT_OWNED,
    )

    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        claimed = claim(client, token)
        single = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/heartbeat",
            headers={"X-Agent-Worker-Token": token, "X-Agent-Lease-Id": "not-the-lease"},
        )
        assert single.status_code == 409

    rejected = _heartbeat_events(events)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == HEARTBEAT_NOT_OWNED
    assert rejected[0]["execution_id"] == claimed["execution_id"]
    # The literals the batch path classifies with are the same strings.
    assert HEARTBEAT_NOT_OWNED == "not_owned"
    assert HEARTBEAT_LEASE_NOT_ACTIVE == "lease_not_active"


def test_batch_heartbeat_takes_row_locks_in_sorted_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """#5125358408 P1-B：批内 AER 行锁按 execution_id（主键）排序获取——
    两个并发批量事务共享同一 worker 注册（运维事故场景）时锁序一致，
    不再 A,B / B,A 交错。钉住结构防回退。"""
    import contextlib

    from server.app.agent_broker import heartbeat_batch as hb

    visited: list[str] = []

    @contextlib.contextmanager
    def _fake_transaction(dsn):
        yield object()

    def _recording_renew_one(conn, broker, worker_id, execution_id, lease_id):
        visited.append(execution_id)
        return None  # everything renews

    monkeypatch.setattr(hb, "write_transaction", _fake_transaction)
    monkeypatch.setattr(hb, "_renew_one", _recording_renew_one)
    monkeypatch.setattr(hb, "touch_worker", lambda conn, worker_id: None)

    class _Broker:
        database_dsn = "unused"

    # Deliberately unsorted input order.
    outcome = hb.batch_heartbeat(
        _Broker(),
        "w",
        [
            {"execution_id": "exec-c", "lease_id": "l"},
            {"execution_id": "exec-a", "lease_id": "l"},
            {"execution_id": "exec-b", "lease_id": "l"},
        ],
    )

    assert visited == ["exec-a", "exec-b", "exec-c"], "row locks must be taken in PK order"
    assert outcome["renewed"] == ["exec-a", "exec-b", "exec-c"]
    assert outcome["lost"] == []
