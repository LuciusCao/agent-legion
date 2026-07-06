from __future__ import annotations

import pytest


@pytest.fixture
def workspace_and_job(client):
    ws_response = client.post(
        "/api/workspaces",
        json={"name": "token_ws", "default_workflow_key": "question_comprehension_info"},
    )
    assert ws_response.status_code == 200
    workspace_id = ws_response.json()["workspace"]["id"]
    response = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q001"],
            "knowledge_codes": [],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["jobs"][0]["id"]
    return workspace_id, job_id


def _insert_node_run(job_db, *, run_id, job_id, node_key, status="completed"):
    with job_db.connect() as conn:
        conn.execute(
            "insert or replace into node_runs(id, job_id, node_key, status) values (?, ?, ?, ?)",
            (run_id, job_id, node_key, status),
        )


def _insert_token_usage(
    job_db,
    *,
    node_run_id,
    job_id,
    workspace_id,
    node_key,
    provider,
    model,
    skill_version,
    input_tokens,
    output_tokens,
    cache_read_tokens,
):
    total = input_tokens + output_tokens + cache_read_tokens
    with job_db.connect() as conn:
        conn.execute(
            """
            insert or replace into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_run_id,
                job_id,
                workspace_id,
                node_key,
                provider,
                model,
                skill_version,
                1,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                total,
            ),
        )


def test_get_run_token_usage_missing_job(client):
    response = client.get("/api/jobs/missing/runs/1/token-usage")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_get_run_token_usage_missing_run(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    response = client.get(f"/api/jobs/{job_id}/runs/999/token-usage")
    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_get_run_token_usage_no_usage_returns_empty_usage(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=1, job_id=job_id, node_key="node-a")

    response = client.get(f"/api/jobs/{job_id}/runs/1/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["run_id"] == 1
    assert body["usage"] is None
    assert body["reason"] == "no token usage recorded for run"


def test_get_run_token_usage_returns_usage_and_cost(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=2, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=2,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-b",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=1000000,
        output_tokens=500000,
        cache_read_tokens=200000,
    )

    response = client.get(f"/api/jobs/{job_id}/runs/2/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["run_id"] == 2
    usage = body["usage"]
    assert usage is not None
    assert usage["provider"] == "gateway"
    assert usage["model"] == "your-model-a"
    assert usage["input_tokens"] == 1000000
    assert usage["output_tokens"] == 500000
    assert usage["cache_read_tokens"] == 200000
    assert usage["total_tokens"] == 1700000
    assert usage["cost"]["currency"] == "CNY"
    assert usage["cost"]["pricing_missing"] is False
    assert usage["cost"]["total"] == pytest.approx(3.0 + 7.5 + 0.12)
    assert body["reason"] is None


def test_get_job_token_usage_missing_job(client):
    response = client.get("/api/jobs/missing/token-usage")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_get_job_token_usage_empty_job(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    response = client.get(f"/api/jobs/{job_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["runs"] == []
    assert body["runs_with_usage"] == 0
    assert body["runs_without_usage"] == 0
    assert body["currency"] == "CNY"


def test_get_job_token_usage_aggregates_runs(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=10, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=11, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=10,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )

    response = client.get(f"/api/jobs/{job_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["runs_with_usage"] == 1
    assert body["runs_without_usage"] == 1
    assert len(body["runs"]) == 2
    with_usage = next(r for r in body["runs"] if r["run_id"] == 10)
    without_usage = next(r for r in body["runs"] if r["run_id"] == 11)
    assert with_usage["usage"] is not None
    assert without_usage["usage"] is None
    assert body["total"]["input_tokens"] == 100
    assert body["total"]["output_tokens"] == 50
    assert body["total"]["cache_read_tokens"] == 10
    assert body["total"]["total_tokens"] == 160
    assert body["total"]["cost"]["total"] == pytest.approx(0.0003 + 0.00075 + 0.000006)


def test_get_workspace_token_usage_missing_workspace(client):
    response = client.get("/api/workspaces/missing/token-usage")
    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


def test_get_workspace_token_usage_empty_workspace(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    response = client.get(f"/api/workspaces/{workspace_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == workspace_id
    assert body["groups"] == []
    assert body["runs_with_usage"] == 0
    assert body["runs_without_usage"] == 0
    assert body["currency"] == "CNY"


def test_get_workspace_token_usage_groups_by_node(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=20, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=21, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=22, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=20,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )
    _insert_token_usage(
        job_db,
        node_run_id=21,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == workspace_id
    assert body["runs_with_usage"] == 2
    assert body["runs_without_usage"] == 1
    assert body["summary"]["input_tokens"] == 300
    assert body["summary"]["output_tokens"] == 150
    assert body["summary"]["total_tokens"] == 480

    groups = {g["group_key"]: g for g in body["groups"]}
    assert "node-a" in groups
    assert groups["node-a"]["runs"] == 2
    assert groups["node-a"]["total_input_tokens"] == 300
    assert groups["node-a"]["node_key"] == "node-a"
    assert groups["node-a"]["provider"] == ""


def test_get_workspace_token_usage_groups_by_provider(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=30, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=31, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=30,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )
    _insert_token_usage(
        job_db,
        node_run_id=31,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-b",
        provider="doubao",
        model="Doubao-Seed-2.1-turbo",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?group_by=provider")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group_key"]: g for g in body["groups"]}
    assert set(groups) == {"gateway", "doubao"}
    assert groups["gateway"]["provider"] == "gateway"
    assert groups["gateway"]["node_key"] == ""
    assert groups["gateway"]["runs"] == 1


def test_get_workspace_token_usage_filters_by_node_key(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=40, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=41, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=40,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )
    _insert_token_usage(
        job_db,
        node_run_id=41,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-b",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?node_key=node-a")
    assert response.status_code == 200
    body = response.json()
    assert len(body["groups"]) == 1
    assert body["groups"][0]["group_key"] == "node-a"
    assert body["summary"]["input_tokens"] == 100
    assert body["runs_without_usage"] == 0


def test_get_workspace_token_usage_filter_excludes_other_provider_runs(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=50, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=51, job_id=job_id, node_key="node-b")
    _insert_node_run(job_db, run_id=52, job_id=job_id, node_key="node-c")
    _insert_token_usage(
        job_db,
        node_run_id=50,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )
    _insert_token_usage(
        job_db,
        node_run_id=51,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-b",
        provider="doubao",
        model="Doubao-Seed-2.1-turbo",
        skill_version="v1",
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?provider=gateway")
    assert response.status_code == 200
    body = response.json()
    assert body["runs_with_usage"] == 1
    assert body["runs_without_usage"] == 1
    assert body["groups"][0]["runs"] == 1
    # coverage denominator excludes the run whose usage is under a different provider.
    assert body["groups"][0]["coverage"] == 0.5
