def _create_workspace(client, name="default"):
    return client.post("/api/workspaces", json={"name": name}).json()["workspace"]["id"]


def test_workspace_stats_hidden_when_workflows_disabled(client_factory):
    with client_factory(workflows_enabled=False) as c:
        response = c.get("/api/workspaces/default/stats")
    assert response.status_code == 404


def test_workspace_stats_returns_counts_and_executor_status(client_factory, monkeypatch):
    from server.app.cms.question import CmsQuestionDetail

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        return CmsQuestionDetail(
            question_id=question_id,
            title=f"Question {question_id}",
            normalized={},
            payload={"uuid": question_id},
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    with client_factory(workflows_enabled=True) as c:
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q301", "Q302"],
                "knowledge_codes": [],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["workspace_id"] == ws_id
    assert body["name"] == "Stats WS"
    assert body["workflow_key"] == "question_comprehension_info"
    assert body["workflow_label"] == "题目审题信息生成 DAG"
    assert body["job_stats"]["pending"] == 2
    assert "queued" not in body["job_stats"]
    assert body["executor_status"]["executors"] == []
    assert body["latest_run"] is None


def test_workspace_stats_executor_status_reflects_allocations_and_leases(
    client_factory, monkeypatch
):
    from server.app.cms.question import CmsQuestionDetail

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        return CmsQuestionDetail(
            question_id=question_id,
            title=f"Reading {question_id}",
            normalized={},
            payload={"uuid": question_id},
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    with client_factory(workflows_enabled=True) as c:
        job_db = c.app.state.job_db
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        job_db.replace_workspace_executor_configuration(
            ws_id,
            allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
            bindings=[
                {
                    "workflow_key": "reading_analysis",
                    "node_key": "review_keywords",
                    "executor_id": "local-default",
                }
            ],
            node_limits=[],
        )
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q301"],
                "knowledge_codes": [],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    executors = body["executor_status"]["executors"]
    assert len(executors) == 1
    assert executors[0]["executor_id"] == "local-default"
    assert executors[0]["kind"] == "local"
    assert executors[0]["global_capacity"] == 16
    assert executors[0]["workspace_limit"] == 4
    assert executors[0]["running"] == 0
    assert executors[0]["available"] == 4
    assert executors[0]["binding_count"] == 1


def test_workspace_stats_latest_run_reflects_node_runs(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q401"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db = c.app.state.job_db
        run = job_db.start_node_run(job_id, "question_understanding", ["echo", "hi"], "/dev/null")
        job_db.finish_node_run(run["id"], "completed", 0, "")
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["latest_run"] is not None
    assert body["latest_run"]["job_id"] == job_id
    assert body["latest_run"]["node_key"] == "question_understanding"
    assert body["latest_run"]["status"] == "completed"


def test_workspace_stats_returns_404_for_unknown_workspace(client_factory):
    with client_factory(workflows_enabled=True) as c:
        resp = c.get("/api/workspaces/nonexistent/stats")
    assert resp.status_code == 404
