from tests.helpers.auth import authenticate_client


def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def _create_job(client, workspace_id: str, question_id: str) -> str:
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": [question_id],
            "knowledge_codes": [],
        },
    ).json()
    return created["jobs"][0]["id"]


def _fail_node(app, job_id: str, node_key: str, category: str, detail: str) -> None:
    job_db = app.state.job_db
    run = job_db.start_node_run(job_id, node_key, ["cmd"], f"logs/jobs/{job_id}-{node_key}.log")
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            """
            update node_runs
            set status='failed', error_message='boom', failure_category=?, failure_detail=?,
                finished_at=current_timestamp
            where id=?
            """,
            (category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message='boom' where job_id=? and node_key=?",
            (job_id, node_key),
        )
        conn.execute("update jobs set status='failed' where id=?", (job_id,))
        conn.execute("commit")


def _app(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return app


def test_list_failed_node_runs_filters_by_category(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q901")
        _fail_node(app, job_id, "clean_and_parse", "technical", "provider_stream")
        _fail_node(app, job_id, "review_key_info", "business", "review_rejected")

        all_runs = c.get(f"/api/workspaces/{ws_id}/failed-node-runs")
        technical = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?category=technical")
        by_detail = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?detail=review_rejected")

    assert all_runs.status_code == 200
    assert {r["node_key"] for r in all_runs.json()["runs"]} == {
        "clean_and_parse",
        "review_key_info",
    }
    assert technical.status_code == 200
    technical_runs = technical.json()["runs"]
    assert len(technical_runs) == 1
    assert technical_runs[0]["job_id"] == job_id
    assert technical_runs[0]["node_key"] == "clean_and_parse"
    assert technical_runs[0]["failure_category"] == "technical"
    assert technical_runs[0]["failure_detail"] == "provider_stream"
    assert technical_runs[0]["error_message"] == "boom"
    assert by_detail.json()["runs"][0]["node_key"] == "review_key_info"


def test_rerun_by_failure_route_reruns_matching_jobs(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q902")
        _fail_node(app, job_id, "review_key_info", "business", "review_rejected")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "business"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["job_id"] == job_id
    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["generate_key_info"]
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["generate_key_info"] == "pending"
    assert nodes["review_key_info"] == "stale"


def test_rerun_by_failure_route_validates_category(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "bogus"},
        )
    assert response.status_code == 422
