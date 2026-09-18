from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def client(client_factory):
    """Private app per test: OpsMetricsService keeps a 5s in-memory summary
    cache; on the worker-session shared app it survives across tests and
    serves stale summaries."""
    with client_factory(fresh=True) as c:
        yield c


def test_metrics_overview_empty_response_shape(client) -> None:
    response = client.get("/api/metrics/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["granularity"] == "6h"
    assert isinstance(body["buckets"], list)


def test_metrics_overview_returns_inserted_buckets(client) -> None:
    # Use a bucket older than the sampler's last-completed-minute target so the
    # background ops-metrics loop cannot upsert over the inserted row.
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into ops_metric_samples(
              bucket_start, online_workers, active_executions,
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, 2, 1, 10, 5, 1, 16)
            """,
            (bucket,),
        )
    response = client.get("/api/metrics/overview?granularity=6h")
    assert response.status_code == 200
    body = response.json()
    # The app's background sampler keeps writing its own minute rows into the
    # shared window, so locate the inserted bucket instead of counting rows.
    rows = [r for r in body["buckets"] if r["bucket_start"] == bucket.isoformat()]
    assert len(rows) == 1
    row = rows[0]
    assert row["online_workers"] == 2
    assert row["online_workers_max"] == 2
    assert row["active_executions"] == 1
    assert row["total_tokens"] == 16


def test_metrics_overview_rejects_invalid_granularity(client) -> None:
    response = client.get("/api/metrics/overview?granularity=week")
    assert response.status_code == 422


def test_metrics_overview_passes_worker_id_filter(client) -> None:
    # Same background-sampler guard as above: insert an old bucket, and the
    # sampler's own global rows are excluded by the worker_id filter anyway.
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into ops_metric_samples(
              bucket_start, worker_id, online_workers, active_executions,
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, 'w-1', 1, 1, 10, 5, 1, 16)
            """,
            (bucket,),
        )
    response = client.get("/api/metrics/overview?granularity=6h&worker_id=w-1")
    assert response.status_code == 200
    rows = [r for r in response.json()["buckets"] if r["bucket_start"] == bucket.isoformat()]
    assert len(rows) == 1
    assert rows[0]["online_workers"] == 1
    assert rows[0]["total_tokens"] == 16


def test_metrics_overview_accepts_all_windows(client) -> None:
    for granularity in ("6h", "24h", "30d"):
        response = client.get(f"/api/metrics/overview?granularity={granularity}")
        assert response.status_code == 200
        assert response.json()["granularity"] == granularity


def test_metrics_overview_summary_shape_and_window_independence(client) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ops-ws', 'Ops', 'demo_workflow') on conflict(id) do nothing",
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('job-1', 'ops-ws', 'question', 'job-1')"
            " on conflict(id) do nothing",
        )
        for node_key, status, started, finished in (
            ("generate", "completed", now - timedelta(seconds=20), now - timedelta(seconds=10)),
            ("review", "failed", now - timedelta(seconds=50), now - timedelta(seconds=40)),
        ):
            run = conn.execute(
                "insert into node_runs(job_id, node_key, status, started_at, finished_at)"
                " values ('job-1', %s, %s, %s, %s) returning id",
                (node_key, status, started, finished),
            ).fetchone()
            # Agent runs 口径：只有被 agent_execution_requests 引用的 run 才计入摘要。
            conn.execute(
                """
                insert into agent_execution_requests(execution_id, workspace_id, job_id, node_key, agent_id, agent_definition_hash, node_concurrency_limit, state, queued_at, node_run_id, manifest_json) values (%s, 'ops-ws', 'job-1', %s, 'agent-1', 'hash', 1, 'done', %s, %s, '{}')
                """,
                (f"exec-{node_key}", node_key, started, run["id"]),
            )

    summaries = []
    for granularity in ("6h", "24h", "30d"):
        response = client.get(f"/api/metrics/overview?granularity={granularity}")
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert set(summary) == {
            "online_workers",
            "active_executions",
            "recent_hour_tokens",
            "recent_hour_runs",
            "queue",
            "queue_alert",
        }
        assert set(summary["queue"]) == {
            "queued",
            "oldest_queued_at",
            "recent_hour_unclaimable_failed",
        }
        runs = summary["recent_hour_runs"]
        assert runs["completed"] == 1
        assert runs["failed"] == 1
        assert runs["duration_p50_seconds"] == 10.0
        assert runs["duration_p95_seconds"] == 10.0
        summaries.append(summary)
    # 采样器只写 ops_metric_samples，不动 node_runs：runs 摘要跨窗口严格一致。
    assert [s["recent_hour_runs"] for s in summaries] == [summaries[0]["recent_hour_runs"]] * 3


def test_metrics_overview_passes_workspace_id_filter(client) -> None:
    # 与 worker_id 过滤同一思路：旧桶 + ws 过滤避开后台采样器的行。
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into ops_metric_samples(bucket_start, workspace_id, queued)"
            " values (%s, 'ops-ws', 7), (%s, '', 99)",
            (bucket, bucket),
        )
    response = client.get("/api/metrics/overview?granularity=6h&workspace_id=ops-ws")
    assert response.status_code == 200
    rows = [r for r in response.json()["buckets"] if r["bucket_start"] == bucket.isoformat()]
    assert len(rows) == 1
    assert rows[0]["queued"] == 7


def test_metrics_scoped_member_token_reads_own_workspace(client, job_db) -> None:
    """codex P2-2 on #626 (PR #704), HTTP pin: a studio-agent scoped token
    minted for a NON-admin member keeps its pre-#626 read access to the
    minter's workspace metrics. require_workspace_access passes it (the
    scoped identity carries user['id']), so the membership guard here must
    resolve the member row instead of 404ing every non-empty actor_scope."""
    from server.app.auth import scoped_tokens

    member = client.post(
        "/api/users",
        json={"username": "metrics_member", "password": "pw-metrics"},
        headers={"x-agent-legion-request": "1"},
    )
    assert member.status_code == 201, member.text
    member_id = member.json()["id"]
    created = client.post(
        "/api/workspaces",
        json={"id": "ops-metrics-ws", "name": "Metrics Scoped WS"},
    )
    assert created.status_code == 200, created.text
    other = client.post(
        "/api/workspaces",
        json={"id": "ops-other-ws", "name": "Metrics Other WS"},
    )
    assert other.status_code == 200, other.text
    job_db.upsert_workspace_member("ops-metrics-ws", member_id, "viewer")

    token = scoped_tokens.mint_scoped_token(job_db, member_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    # Own workspace (member): the read passes with real buckets.
    response = scoped.get("/api/metrics/overview?workspace_id=ops-metrics-ws")
    assert response.status_code == 200, response.text
    assert response.json()["granularity"] == "6h"
    # Global scope stays admin-only for the scoped member (403), and a
    # workspace the minter does not belong to stays 404 — both unchanged.
    assert scoped.get("/api/metrics/overview").status_code == 403
    assert scoped.get("/api/metrics/overview?workspace_id=ops-other-ws").status_code == 404


def test_metrics_workspace_membership_guard() -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from server.app.routes.metrics_access import enforce_workspace_membership

    def _request(role):
        job_db = SimpleNamespace(get_workspace_role=lambda ws, uid: role)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_db=job_db)))

    # admin 直通；成员带 workspace_id 校验成员资格；非成员 404。
    enforce_workspace_membership(_request(None), "ops-ws", {"role": "admin", "id": "u1"})
    enforce_workspace_membership(_request(None), None, {"role": "admin", "id": "u1"})
    enforce_workspace_membership(_request("viewer"), "ops-ws", {"role": "member", "id": "u1"})
    with pytest.raises(HTTPException) as exc_info:
        enforce_workspace_membership(_request(None), "ops-ws", {"role": "member", "id": "u1"})
    assert exc_info.value.status_code == 404
    # 全局视图（无 workspace_id）对成员 403：只能看所属 workspace。
    with pytest.raises(HTTPException) as exc_info:
        enforce_workspace_membership(_request("viewer"), None, {"role": "member", "id": "u1"})
    assert exc_info.value.status_code == 403


def test_metrics_guard_api_scope_only_no_wider_scopes() -> None:
    """codex P2-2 on #626 (PR #704), unit pin: the machine-identity arm in
    enforce_workspace_membership must fire for actor_scope='api' ONLY. A
    studio-agent scoped token carries the initiating user's row — it goes
    through the normal member lookup (viewer passes, non-member 404), not a
    blanket scoped-identity 404."""
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE
    from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE
    from server.app.routes.metrics_access import enforce_workspace_membership

    def _request(role):
        job_db = SimpleNamespace(get_workspace_role=lambda ws, uid: role)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_db=job_db)))

    studio_agent = {"role": "member", "id": "u1", "actor_scope": STUDIO_AGENT_SCOPE}
    # Member of the addressed workspace: the read passes (pre-#626 behavior).
    enforce_workspace_membership(_request("viewer"), "ops-ws", studio_agent)
    # Not a member: the same 404 as any non-member user session.
    with pytest.raises(HTTPException) as exc_info:
        enforce_workspace_membership(_request(None), "ops-ws", studio_agent)
    assert exc_info.value.status_code == 404
    # The api machine identity stays refused regardless of the member rows —
    # it never reaches the lookup (defense in depth; the allowlist guard in
    # require_workspace_access has already 404'd it on the real route).
    with pytest.raises(HTTPException) as exc_info:
        enforce_workspace_membership(
            _request("viewer"), "ops-ws", {"actor_scope": WORKSPACE_API_SCOPE}
        )
    assert exc_info.value.status_code == 404
