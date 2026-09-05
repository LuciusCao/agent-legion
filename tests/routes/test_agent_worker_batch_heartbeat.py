"""Route tests for the per-Worker batch heartbeat endpoint (#352).

``POST /api/agent-executions/heartbeats`` renews every listed lease of the
authenticated Worker in one write transaction: a batch fully renewed, a
partial batch (unknown/expired ids answered per item, never 5xx), the empty
batch, auth (worker token only, and only this Worker's leases renew — a
foreign Worker's execution is lost), the batch size cap, and the cancel body
for code executions. The single heartbeat endpoint's behavior is pinned by
tests/routes/test_agent_workers.py and stays untouched here.
"""

from __future__ import annotations

from pathlib import Path

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
    原持有者的租约不受影响。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        owner_token = register(client)["worker_token"]
        claimed = claim(client, owner_token)
        intruder_token = _register_second_worker(client)

        outcome = _heartbeat_ok(
            client,
            intruder_token,
            [{"execution_id": claimed["execution_id"], "lease_id": claimed["lease_id"]}],
        )

    assert outcome["renewed"] == []
    assert outcome["lost"] == [claimed["execution_id"]]


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
