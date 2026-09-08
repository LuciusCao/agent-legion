"""Batch claim route tests (issue #546).

``POST /api/agent-executions/claim`` with ``limit > 1`` promotes up to
``limit`` executions in one transaction and answers ``{"claims": [...]}``
(empty batch = the same 204); the default ``limit = 1`` keeps the legacy
single-claim response byte-identical (no ``claims`` wrapper).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from shared.protocol import PROTOCOL_VERSION
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    enqueue_code,
    insert_code_job_rows,
    make_app,
    register,
    seed_request,
)

_CLAIM_URL = "/api/agent-executions/claim"

_SINGLE_CLAIM_KEYS = {
    "execution_id",
    "lease_id",
    "workspace_id",
    "job_id",
    "workflow_key",
    "node_key",
    "agent_id",
    "kind",
    "manifest",
    "bundle_url",
}


def _batch_claim(client: TestClient, token: str, payload: dict) -> object:
    response = client.post(
        _CLAIM_URL,
        headers={"X-Agent-Worker-Token": token},
        json={"worker_id": "home-mini", **payload},
    )
    return response


def test_batch_claim_returns_claims_list(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for index in range(3):
        seed_request(app.state.job_db, job_id=f"job-{index}", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        response = _batch_claim(client, token, {"limit": 3})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"claims"}
    claims = body["claims"]
    assert len(claims) == 3
    assert {claim["job_id"] for claim in claims} == {"job-0", "job-1", "job-2"}
    assert all(set(claim) == _SINGLE_CLAIM_KEYS for claim in claims)
    assert len({claim["lease_id"] for claim in claims}) == 3


def test_single_claim_response_shape_unchanged(tmp_path: Path) -> None:
    """协议兼容回归：不发 limit（或 limit=1）时响应逐字段同 0.7.3——单对象、
    无 claims 包装。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=2)
    seed_request(app.state.job_db, job_id="job-2", limit=2)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        for payload in ({"worker_id": "home-mini"}, {"worker_id": "home-mini", "limit": 1}):
            response = client.post(
                _CLAIM_URL,
                headers={"X-Agent-Worker-Token": token},
                json=payload,
            )
            assert response.status_code == 200, response.text
            assert set(response.json()) == _SINGLE_CLAIM_KEYS


def test_batch_claim_empty_queue_is_204(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        response = _batch_claim(client, token, {"limit": 8})

    assert response.status_code == 204


def test_batch_claim_respects_per_pool_limits(tmp_path: Path) -> None:
    """分池批申请过路由层：agent_limit=1 + code_limit=1 → 一批恰 1+1。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-agent", limit=10)
    insert_code_job_rows(app.state.job_db, job_id="job-code")
    enqueue_code(app.state.agent_broker, job_id="job-code")

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(
            client,
            protocol_version=PROTOCOL_VERSION,
            max_code_concurrency=4,
            # v3+ 的 model 声明必须带 runtime（注册校验）。
            models=[{"provider": "gateway", "model": "test-model", "runtime": "pi"}],
        )["worker_token"]
        response = _batch_claim(client, token, {"limit": 8, "agent_limit": 1, "code_limit": 1})

    assert response.status_code == 200, response.text
    claims = response.json()["claims"]
    assert sorted(claim["kind"] for claim in claims) == ["agent", "code"]
    assert {claim["job_id"] for claim in claims} == {"job-agent", "job-code"}


def test_batch_claim_rejects_invalid_limit(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        response = _batch_claim(client, token, {"limit": 0})

    assert response.status_code == 422


def test_pool_limits_alone_route_to_batch_path(tmp_path: Path) -> None:
    """携带分池上限即批请求（即使 limit=1）——稳态补位（预算和=1）是最常见
    形态，若只按 limit>1 分流，agent_limit=0 的池在单条路径上完全不受钳制。"""
    app = make_app(tmp_path)
    seed_request(app.state.job_db, job_id="job-1", limit=10)

    with TestClient(app) as client:
        authenticate_admin(client)
        token = register(client)["worker_token"]
        response = _batch_claim(client, token, {"limit": 1, "agent_limit": 1, "code_limit": 0})

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"claims"}
    assert len(response.json()["claims"]) == 1


def test_batch_response_drops_only_the_broken_item() -> None:
    """批内单条注入失败（如 code manifest 解析异常）只剔该条——其余兄弟
    照常交付；全丢才回落 204。纯单元：无需 app/DB。"""
    from server.app.agent_broker.claim_scan import AgentClaim
    from server.app.routes import agent_worker_claim_response as response_module

    def _claim(execution_id: str) -> AgentClaim:
        return AgentClaim(
            execution_id=execution_id,
            workspace_id="ws",
            job_id=f"job-{execution_id}",
            node_key="n",
            agent_id="a",
            lease_id=f"lease-{execution_id}",
            node_run_id=1,
            manifest={},
        )

    original = response_module.build_claim_response

    def flaky(broker, settings, job_artifact_objects, worker, claimed):  # type: ignore[no-untyped-def]
        if claimed.execution_id == "bad":
            raise RuntimeError("manifest resolution exploded")
        return original(broker, settings, job_artifact_objects, worker, claimed)

    response_module.build_claim_response = flaky
    try:
        result = response_module.build_batch_claim_response(
            None, None, None, {"protocol_version": 5}, [_claim("good"), _claim("bad")]
        )
        assert hasattr(result, "claims"), "partial batch must stay a 200 payload"
        assert [item.execution_id for item in result.claims] == ["good"]

        all_dropped = response_module.build_batch_claim_response(
            None, None, None, {"protocol_version": 5}, [_claim("bad")]
        )
    finally:
        response_module.build_claim_response = original
    assert all_dropped.status_code == 204, "all items dropped = the empty-batch 204"
