"""POST /api/workspaces/{id}/jobs/batch-pause|batch-resume 路由契约。"""

from tests.helpers.auth import authenticate_client


def _create_workspace(client, name="default"):
    return client.post(
        "/api/workspaces",
        json={
            "name": name,
            "default_workflow_key": "education_video_problems_generation",
        },
    ).json()["workspace"]["id"]


def _create_job(client, ws_id, source_id):
    created = client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "education_video_problems_generation",
            "source_kind": "direct_ids",
            "knowledge_point_ids": [source_id],
        },
    ).json()
    return created["jobs"][0]["id"]


def _make_client(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return authenticate_client(TestClient(app))


def test_batch_pause_and_resume_round_trip(tmp_path):
    with _make_client(tmp_path) as c:
        ws_id = _create_workspace(c)
        job_ids = [_create_job(c, ws_id, "Q901"), _create_job(c, ws_id, "Q902")]

        pause = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-pause",
            json={"job_ids": job_ids, "reason": "smoke hold"},
        )
        assert pause.status_code == 200
        results = pause.json()["results"]
        assert [r["status"] for r in results] == ["succeeded", "succeeded"]
        assert {r["operation"] for r in results} == {"pause"}

        detail = c.get(f"/api/jobs/{job_ids[0]}").json()["job"]
        assert detail["execution_control"]["paused"] is True
        assert "smoke hold" in detail["execution_control"]["pause_reason"]
        assert "user:" in detail["execution_control"]["pause_reason"]

        snapshot = c.get(f"/api/workspaces/{ws_id}/jobs/snapshot", params={"paused": True}).json()
        assert {job["id"] for job in snapshot["jobs"]} == set(job_ids)
        unpaused = c.get(f"/api/workspaces/{ws_id}/jobs/snapshot", params={"paused": False}).json()
        assert unpaused["jobs"] == []

        resume = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-resume",
            json={"job_ids": job_ids},
        )
        assert resume.status_code == 200
        assert [r["status"] for r in resume.json()["results"]] == [
            "succeeded",
            "succeeded",
        ]
        detail = c.get(f"/api/jobs/{job_ids[0]}").json()["job"]
        assert detail["execution_control"]["paused"] is False
        assert detail["execution_control"]["pause_reason"] == ""


def test_batch_pause_with_filter_and_exclude(tmp_path):
    with _make_client(tmp_path) as c:
        ws_id = _create_workspace(c)
        kept = _create_job(c, ws_id, "Q911")
        excluded = _create_job(c, ws_id, "Q912")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-pause",
            json={
                "filter": {"status": "pending"},
                "exclude_ids": [excluded],
            },
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["job_id"] for r in results] == [kept]
        assert results[0]["status"] == "succeeded"
        assert c.get(f"/api/jobs/{excluded}").json()["job"]["execution_control"]["paused"] is False


def test_batch_pause_rejects_ambiguous_selection(tmp_path):
    with _make_client(tmp_path) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q921")
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-pause",
            json={"job_ids": [job_id], "filter": {"status": "pending"}},
        )
        assert response.status_code == 422


def test_batch_pause_skips_terminal_jobs(tmp_path):
    with _make_client(tmp_path) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q931")
        app_job_db = c.app.state.job_db
        app_job_db.update_job_status(job_id, "failed", "boom")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-pause",
            json={"job_ids": [job_id]},
        )
        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["status"] == "skipped"
        assert result["reason_code"] == "terminal"
        assert c.get(f"/api/jobs/{job_id}").json()["job"]["execution_control"]["paused"] is False
