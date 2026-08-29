from tests.helpers import publish_legacy_intake_revision


def _create_workspace(client, name="Stats WS", default_workflow_key="stats_ws"):
    workspace_id = client.post(
        "/api/workspaces", json={"id": default_workflow_key, "name": name}
    ).json()["workspace"]["id"]
    # The demo workflow no longer declares intake modes (#154); these tests
    # post job-batches, so publish the legacy-intake variant.
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def test_workspace_stats_hidden_when_workflows_disabled(client_factory):
    with client_factory(workflows_enabled=False) as c:
        response = c.get("/api/workspaces/default/stats")
    assert response.status_code == 404


def test_workspace_stats_returns_counts_and_executor_status(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "stats_ws", "name": "Stats WS"},
        ).json()
        ws_id = ws["workspace"]["id"]
        publish_legacy_intake_revision(c.app.state.job_db, ws_id)
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "stats_ws",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q301", "Q302"],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["workspace_id"] == ws_id
    assert body["name"] == "Stats WS"
    assert body["workflow_key"] == "stats_ws"
    assert body["workflow_label"] == "教学视频脚本与题目生成（示例）"
    assert body["job_stats"]["pending"] == 2
    assert "queued" not in body["job_stats"]
    assert body["code_pool"] == {"capacity": 16, "running": 0, "available": 16}
    assert body["latest_run"] is None


def test_workspace_stats_code_pool_reflects_leases(client_factory):
    with client_factory(workflows_enabled=True) as c:
        job_db = c.app.state.job_db
        ws = c.post(
            "/api/workspaces",
            json={"id": "stats_ws", "name": "Stats WS"},
        ).json()
        ws_id = ws["workspace"]["id"]
        publish_legacy_intake_revision(c.app.state.job_db, ws_id)
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "stats_ws",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q301"],
            },
        )
        # A live code-pool lease moves running/available (P-0.5: single pool).
        from server.app.executors.leases import ExecutorLeaseRepository
        from server.app.executors.models import LeaseClaimRequest

        job_id = job_db.list_jobs(workspace_id=ws_id)[0]["id"]
        repo = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
        claim = repo.try_claim(
            LeaseClaimRequest(
                executor_id="code",
                global_capacity=16,
                workspace_id=ws_id,
                job_id=str(job_id),
                workflow_key="education_video_problems_generation",
                node_key="intake_knowledge_points",
                capability="intake_knowledge_points",
                local_node_limit=None,
                lease_ttl_seconds=60,
                log_path="logs/run.log",
            )
        )
        assert claim is not None
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["code_pool"] == {"capacity": 16, "running": 1, "available": 15}


def test_workspace_stats_latest_run_reflects_node_runs(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "stats_ws",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q401"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db = c.app.state.job_db
        run = job_db.start_node_run(job_id, "write_script", ["echo", "hi"], "/dev/null")
        job_db.finish_node_run(run["id"], "completed", 0, "")
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["latest_run"] is not None
    assert body["latest_run"]["job_id"] == job_id
    assert body["latest_run"]["node_key"] == "write_script"
    assert body["latest_run"]["status"] == "completed"


def test_workspace_stats_returns_404_for_unknown_workspace(client_factory):
    with client_factory(workflows_enabled=True) as c:
        resp = c.get("/api/workspaces/nonexistent/stats")
    assert resp.status_code == 404
