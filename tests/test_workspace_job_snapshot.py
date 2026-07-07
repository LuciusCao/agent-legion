def test_workspace_jobs_snapshot_returns_jobs_stats_and_revision(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = job_db.create_workspace(
            "snapshot-ws", default_workflow_key="question_comprehension_info"
        )
        job_db.create_job(
            workspace_id=workspace["id"],
            workflow_key="question_comprehension_info",
            source_type="question_id",
            source_id="q1",
            batch_id="",
            title="Question 1",
            node_keys=["fetch_question_context"],
        )

        response = client.get(f"/api/workspaces/{workspace['id']}/jobs/snapshot?limit=20")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace_id"] == workspace["id"]
    assert isinstance(data["revision"], int)
    assert "stats" in data
    assert len(data["jobs"]) == 1
    assert data["next_cursor"] is None
