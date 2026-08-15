from __future__ import annotations

import pytest

CSRF = {"x-agent-legion-request": "1"}
PRICING_URL = "/api/admin/token-usage-pricing"


def _member_client(client, username="pricing_member", password="pw1"):
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _payload() -> dict:
    return {
        "currency": "USD",
        "pricing": [
            {
                "provider": "gateway",
                "model": "your-model-a",
                "input_per_1m": 10.0,
                "output_per_1m": 20.0,
                "cache_read_per_1m": 1.0,
            }
        ],
    }


def test_get_requires_auth(anon_client) -> None:
    assert anon_client.get(PRICING_URL).status_code == 401


def test_member_forbidden(client) -> None:
    member = _member_client(client)
    assert member.get(PRICING_URL).status_code == 403
    assert member.put(PRICING_URL, json=_payload()).status_code == 403


def test_get_returns_seeded_test_pricing(client) -> None:
    # conftest seeds a deterministic pricing document after every TRUNCATE.
    response = client.get(PRICING_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "CNY"
    assert {row["model"] for row in body["pricing"]} >= {"your-model-a", "your-model-b"}


def test_put_roundtrip(client) -> None:
    response = client.put(PRICING_URL, json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"
    assert body["pricing"] == _payload()["pricing"]

    response = client.get(PRICING_URL)
    assert response.json() == body


def test_put_rejects_negative_rates(client) -> None:
    payload = _payload()
    payload["pricing"][0]["input_per_1m"] = -1.0
    assert client.put(PRICING_URL, json=payload).status_code == 422


def test_put_rejects_empty_provider(client) -> None:
    payload = _payload()
    payload["pricing"][0]["provider"] = ""
    assert client.put(PRICING_URL, json=payload).status_code == 422


@pytest.fixture
def workspace_usage(client):
    """Seed one workspace/job/run with usage priced by gateway/your-model-a."""
    job_db = client.app.state.job_db
    workspace_id = "pricing_ws"
    job_id = "pricing_job_1"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, %s)"
            " on conflict (id) do nothing",
            (workspace_id, "pricing_ws", "demo_workflow"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (%s, %s, %s, %s, %s) on conflict (id) do nothing",
            (job_id, workspace_id, "demo_workflow", "batch_by_ids", "Q001"),
        )
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)",
            (1, job_id, "node-a", "completed"),
        )
        conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                1,
                job_id,
                workspace_id,
                "node-a",
                "gateway",
                "your-model-a",
                "v1",
                1,
                1_000_000,
                1_000_000,
                0,
                2_000_000,
            ),
        )
    return workspace_id


def _workspace_total_cost(client, workspace_id: str) -> float:
    response = client.get(f"/api/workspaces/{workspace_id}/token-usage")
    assert response.status_code == 200
    return response.json()["summary"]["cost"]["total"]


def test_database_pricing_drives_workspace_cost(client, workspace_usage) -> None:
    # seeded rates: input 3.0 / output 15.0 per 1M → 1M in + 1M out = 18.0
    assert _workspace_total_cost(client, workspace_usage) == pytest.approx(18.0)

    assert client.put(PRICING_URL, json=_payload()).status_code == 200
    # updated rates: input 10.0 / output 20.0 per 1M → 30.0
    assert _workspace_total_cost(client, workspace_usage) == pytest.approx(30.0)


def test_unpriced_models_keep_known_cost_and_are_listed(client, workspace_usage) -> None:
    """Rows without configured pricing are excluded from the total (not zeroing
    it) and reported in pricing_missing_models."""
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)",
            (2, "pricing_job_1", "node-b", "completed"),
        )
        conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                2,
                "pricing_job_1",
                workspace_usage,
                "node-b",
                "gateway",
                "unpriced-model",
                "v1",
                1,
                500_000,
                500_000,
                0,
                1_000_000,
            ),
        )

    response = client.get(f"/api/workspaces/{workspace_usage}/token-usage")
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["pricing_missing"] is True
    assert summary["pricing_missing_models"] == ["gateway/unpriced-model"]
    # Known cost is still computed from the priced rows only.
    assert summary["cost"]["total"] == pytest.approx(18.0)

    response = client.get("/api/jobs/pricing_job_1/token-usage")
    assert response.status_code == 200
    total = response.json()["total"]
    assert total["pricing_missing"] is True
    assert total["pricing_missing_models"] == ["gateway/unpriced-model"]
    assert total["cost"]["total"] == pytest.approx(18.0)
