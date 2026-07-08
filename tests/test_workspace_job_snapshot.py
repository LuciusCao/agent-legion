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


def test_workspace_jobs_snapshot_paginates_with_cursor(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = job_db.create_workspace(
            "cursor-ws", default_workflow_key="question_comprehension_info"
        )
        created = []
        for i in range(5):
            job = job_db.create_job(
                workspace_id=workspace["id"],
                workflow_key="question_comprehension_info",
                source_type="question_id",
                source_id=f"q{i}",
                batch_id="",
                title=f"Question {i}",
                node_keys=["fetch_question_context"],
            )
            created.append(job["id"])

        first = client.get(f"/api/workspaces/{workspace['id']}/jobs/snapshot?limit=2")
        assert first.status_code == 200
        first_data = first.json()
        assert len(first_data["jobs"]) == 2
        assert first_data["next_cursor"] is not None

        second = client.get(
            f"/api/workspaces/{workspace['id']}/jobs/snapshot?limit=2&cursor={first_data['next_cursor']}"
        )
        assert second.status_code == 200
        second_data = second.json()
        assert len(second_data["jobs"]) == 2
        assert second_data["next_cursor"] is not None

        third = client.get(
            f"/api/workspaces/{workspace['id']}/jobs/snapshot?limit=2&cursor={second_data['next_cursor']}"
        )
        assert third.status_code == 200
        third_data = third.json()
        assert len(third_data["jobs"]) == 1
        assert third_data["next_cursor"] is None

        ids = [job["id"] for page in [first_data, second_data, third_data] for job in page["jobs"]]
        assert sorted(ids) == sorted(created)
