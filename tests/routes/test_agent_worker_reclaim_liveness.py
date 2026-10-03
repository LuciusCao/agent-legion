"""#681: worker-level liveness gates the lease reclaim; refused results are audited.

The sweep's worker-level pre-signal is ``agent_workers.last_seen_at`` (the
#566 deferral) — no new column: every authenticated Worker call refreshes
it. These route tests pin that EACH worker-level channel the issue names
(claim poll, presence sync, another execution's heartbeat) keeps an
execution whose own heartbeat went silent from being reclaimed, that the
deferral stays bounded (2×TTL), and that a whole-Worker silence still
reclaims at the TTL. The result route's 409 precheck writes the
``execution.result_rejected`` audit line.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers.agent_worker_api import (
    authenticate_admin,
    claim,
    empty_archive,
    make_app,
    register,
    seed_request,
)

_AUDIT_LOGGER = "server.app.agent_broker.lease_reclaim_audit"


def _age(app, execution_id: str, silence: float, worker_age: float) -> None:
    """Silence one execution's heartbeat and age the Worker's last contact;
    forget the liveness write throttle as if that much time had passed."""
    with app.state.job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=silence), execution_id),
        )
        conn.execute(
            "update agent_workers set last_seen_at=%s",
            (datetime.now(UTC) - timedelta(seconds=worker_age),),
        )
    app.state.agent_worker_registry._liveness._writes.clear()


def _state(app, execution_id: str) -> str:
    with app.state.job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s", (execution_id,)
        ).fetchone()
    return str(row["state"])


def _interact(client: TestClient, token: str, channel: str, sibling: dict) -> None:
    auth = {"X-Agent-Worker-Token": token}
    if channel == "heartbeat":
        item = {"execution_id": sibling["execution_id"], "lease_id": sibling["lease_id"]}
        response = client.post(
            "/api/agent-executions/heartbeats", headers=auth, json={"executions": [item]}
        )
    elif channel == "presence":
        response = client.post(
            "/api/agent-workers/self/presence", headers=auth, json={"claim_enabled": True}
        )
    else:
        response = client.post(
            "/api/agent-executions/claim", headers=auth, json={"worker_id": "home-mini"}
        )
    assert response.status_code in (200, 204), response.text


def _two_claims(app, client: TestClient) -> tuple[str, dict, dict]:
    seed_request(app.state.job_db, job_id="job-1", limit=10)
    seed_request(app.state.job_db, job_id="job-2", limit=10)
    authenticate_admin(client)
    token = register(client)["worker_token"]
    return token, claim(client, token), claim(client, token)


@pytest.mark.parametrize("channel", ["heartbeat", "presence", "claim"])
def test_any_worker_level_interaction_defers_reclaim(tmp_path: Path, channel: str) -> None:
    app = make_app(tmp_path)
    ttl = app.state.agent_broker.lease_ttl_seconds
    with TestClient(app) as client:
        token, silent, sibling = _two_claims(app, client)
        _age(app, silent["execution_id"], ttl + 10, worker_age=ttl + 10)
        _interact(client, token, channel, sibling)

        assert app.state.agent_broker.sweep_expired_claims() == []
    assert _state(app, silent["execution_id"]) == "claimed"


def test_whole_worker_silence_reclaims_at_ttl(tmp_path: Path) -> None:
    """没有任何 worker 级交互（真死）：TTL 到期即收回，不等宽限。"""
    app = make_app(tmp_path)
    ttl = app.state.agent_broker.lease_ttl_seconds
    with TestClient(app) as client:
        _token, silent, _sibling = _two_claims(app, client)
        _age(app, silent["execution_id"], ttl + 10, worker_age=ttl + 10)

        assert app.state.agent_broker.sweep_expired_claims() == [silent["execution_id"]]
    assert _state(app, silent["execution_id"]) == "queued"


def test_deferral_is_bounded_even_with_live_worker(tmp_path: Path) -> None:
    """有上限：worker 持续交互，但该执行静默超过 2×TTL 仍被收回。"""
    app = make_app(tmp_path)
    ttl = app.state.agent_broker.lease_ttl_seconds
    with TestClient(app) as client:
        token, silent, sibling = _two_claims(app, client)
        _age(app, silent["execution_id"], 2 * ttl + 10, worker_age=ttl + 10)
        _interact(client, token, "presence", sibling)

        assert app.state.agent_broker.sweep_expired_claims() == [silent["execution_id"]]


def test_result_precheck_409_is_audited(tmp_path: Path, caplog) -> None:
    """worker 恢复后上报已完成结果，租约已被收回：409 + 结构化审计行。"""
    app = make_app(tmp_path)
    ttl = app.state.agent_broker.lease_ttl_seconds
    archive = empty_archive()
    with TestClient(app) as client:
        token, silent, _sibling = _two_claims(app, client)
        _age(app, silent["execution_id"], ttl + 10, worker_age=ttl + 10)
        assert app.state.agent_broker.sweep_expired_claims() == [silent["execution_id"]]

        with caplog.at_level(logging.WARNING, logger=_AUDIT_LOGGER):
            response = client.post(
                f"/api/agent-executions/{silent['execution_id']}/result",
                headers={
                    "X-Agent-Worker-Token": token,
                    "X-Agent-Lease-Id": silent["lease_id"],
                    "X-Agent-Result": json.dumps({"status": "completed", "exit_code": 0}),
                },
                content=archive,
            )

    assert response.status_code == 409
    lines = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == _AUDIT_LOGGER and record.levelno == logging.WARNING
    ]
    (line,) = [entry for entry in lines if entry["event"] == "execution.result_rejected"]
    assert line["execution_id"] == silent["execution_id"]
    assert line["worker_id"] == "home-mini"
    assert line["stage"] == "precheck"
    assert line["reason"] == "requeued"
    assert line["carries_artifacts"] is False
    assert line["archive_bytes"] == len(archive)
