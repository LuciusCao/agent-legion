from __future__ import annotations

import pytest


@pytest.fixture
def workspace_and_job(client):
    """Seed workspace and job directly through job_db to avoid CMS calls."""
    job_db = client.app.state.job_db
    workspace_id = "token_ws"
    job_id = "token_job_1"
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, %s)"
            " on conflict (id) do nothing",
            (workspace_id, "token_ws", "demo_workflow"),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id) "
            "values (%s, %s, %s, %s, %s) on conflict (id) do nothing",
            (job_id, workspace_id, "demo_workflow", "batch_by_ids", "Q001"),
        )
    return workspace_id, job_id


def _insert_node_run(job_db, *, run_id, job_id, node_key, status="completed"):
    with job_db.connect() as conn:
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)"
            " on conflict (id) do update set job_id=excluded.job_id,"
            " node_key=excluded.node_key, status=excluded.status",
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
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (node_run_id) do update set
              job_id=excluded.job_id,
              workspace_id=excluded.workspace_id,
              node_key=excluded.node_key,
              provider=excluded.provider,
              model=excluded.model,
              skill_version=excluded.skill_version,
              message_count=excluded.message_count,
              input_tokens=excluded.input_tokens,
              output_tokens=excluded.output_tokens,
              cache_read_tokens=excluded.cache_read_tokens,
              total_tokens=excluded.total_tokens
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
    assert usage["pricing_missing"] is False
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


def test_get_workspace_token_usage_groups_by_model(client, workspace_and_job):
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

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?group_by=model")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group_key"]: g for g in body["groups"]}
    assert set(groups) == {"your-model-a", "Doubao-Seed-2.1-turbo"}
    assert groups["your-model-a"]["model"] == "your-model-a"
    assert groups["your-model-a"]["node_key"] == ""
    assert groups["your-model-a"]["runs"] == 1


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
    # coverage is per-group: node-a has one run and it has usage.
    assert body["groups"][0]["coverage"] == 1.0


def test_get_workspace_token_usage_groups_by_node_skill_version(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=60, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=61, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=62, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=60,
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
        node_run_id=61,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v2",
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )
    _insert_token_usage(
        job_db,
        node_run_id=62,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-b",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=30,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?group_by=node_skill_version")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group_key"]: g for g in body["groups"]}
    assert set(groups) == {"node-a / v1", "node-a / v2", "node-b / v1"}
    assert groups["node-a / v1"]["node_key"] == "node-a"
    assert groups["node-a / v1"]["skill_version"] == "v1"
    assert groups["node-a / v1"]["total_input_tokens"] == 100
    assert groups["node-a / v2"]["total_input_tokens"] == 200
    assert groups["node-b / v1"]["total_input_tokens"] == 300


def test_get_run_token_usage_missing_pricing_returns_null_cost(client, workspace_and_job):
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=3, job_id=job_id, node_key="node-c")
    _insert_token_usage(
        job_db,
        node_run_id=3,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-c",
        provider="unknown",
        model="unknown-model",
        skill_version="v1",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
    )

    response = client.get(f"/api/jobs/{job_id}/runs/3/token-usage")
    assert response.status_code == 200
    body = response.json()
    usage = body["usage"]
    assert usage is not None
    assert usage["cost"] is None
    assert usage["pricing_missing"] is True


def test_get_workspace_token_usage_rejects_invalid_group_by(client, workspace_and_job):
    workspace_id, _job_id = workspace_and_job
    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?group_by=provider")
    assert response.status_code == 422


def test_get_workspace_token_usage_caps_limit(client, workspace_and_job):
    workspace_id, _job_id = workspace_and_job
    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?limit=10000")
    assert response.status_code == 422


def test_get_workspace_token_usage_limit_does_not_cap_totals(client, workspace_and_job):
    """Summary totals must aggregate the full filtered set, not just limited rows."""
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    for i in range(5):
        _insert_node_run(job_db, run_id=100 + i, job_id=job_id, node_key="node-a")
        _insert_token_usage(
            job_db,
            node_run_id=100 + i,
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

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["runs_with_usage"] == 5
    assert body["summary"]["input_tokens"] == 500
    assert body["summary"]["total_tokens"] == 800
    assert len(body["groups"]) == 1


def test_get_workspace_token_usage_coverage_uses_per_group_denominator(client, workspace_and_job):
    """A node with usage for all its own runs should show 100% coverage."""
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=200, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=201, job_id=job_id, node_key="node-b")
    _insert_token_usage(
        job_db,
        node_run_id=200,
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

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group_key"]: g for g in body["groups"]}
    assert groups["node-a"]["coverage"] == 1.0
    assert body["runs_without_usage"] == 1


def test_get_workspace_token_usage_mixed_model_group_cost_is_summed(client, workspace_and_job):
    """A node group with runs under different provider/model pairs prices each pair separately."""
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    _insert_node_run(job_db, run_id=300, job_id=job_id, node_key="node-a")
    _insert_node_run(job_db, run_id=301, job_id=job_id, node_key="node-a")
    _insert_token_usage(
        job_db,
        node_run_id=300,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="gateway",
        model="your-model-a",
        skill_version="v1",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=200_000,
    )
    _insert_token_usage(
        job_db,
        node_run_id=301,
        job_id=job_id,
        workspace_id=workspace_id,
        node_key="node-a",
        provider="doubao",
        model="Doubao-Seed-2.1-turbo",
        skill_version="v1",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=200_000,
    )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group_key"]: g for g in body["groups"]}
    assert len(groups) == 1
    # Both pricing configs use 3.0 / 15.0 / 0.6 per 1M.
    expected = (1_000_000 * 3.0 + 500_000 * 15.0 + 200_000 * 0.6) / 1_000_000
    assert groups["node-a"]["total_cost"] == pytest.approx(expected * 2)
    assert groups["node-a"]["avg_cost"] == pytest.approx(expected)


def test_get_workspace_token_usage_summary_cost_not_capped_by_limit(client, workspace_and_job):
    """Summary cost aggregates the full filtered set, not only the displayed groups."""
    workspace_id, job_id = workspace_and_job
    job_db = client.app.state.job_db
    for i in range(3):
        _insert_node_run(job_db, run_id=400 + i, job_id=job_id, node_key=f"node-{i}")
        _insert_token_usage(
            job_db,
            node_run_id=400 + i,
            job_id=job_id,
            workspace_id=workspace_id,
            node_key=f"node-{i}",
            provider="gateway",
            model="your-model-a",
            skill_version="v1",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=200_000,
        )

    response = client.get(f"/api/workspaces/{workspace_id}/token-usage?limit=1")
    assert response.status_code == 200
    body = response.json()
    # Only one group is returned due to limit, but summary cost covers all 3 runs.
    assert len(body["groups"]) == 1
    expected_run_cost = (1_000_000 * 3.0 + 500_000 * 15.0 + 200_000 * 0.6) / 1_000_000
    assert body["summary"]["cost"]["total"] == pytest.approx(expected_run_cost * 3)
