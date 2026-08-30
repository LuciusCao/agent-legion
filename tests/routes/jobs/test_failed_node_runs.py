from tests.helpers import publish_legacy_intake_revision
from tests.helpers.auth import authenticate_client


def _create_workspace(
    client, name="default", default_workflow_key="education_video_problems_generation"
):
    workspace_id = client.post(
        "/api/workspaces", json={"id": default_workflow_key, "name": name}
    ).json()["workspace"]["id"]
    # The demo workflow no longer declares intake modes (#154); these tests
    # post job-batches, so publish the legacy-intake variant.
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def _create_job(client, workspace_id: str, question_id: str) -> str:
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "education_video_problems_generation",
            "source_kind": "direct_ids",
            "knowledge_point_ids": [question_id],
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
            set status='failed', error_message='boom', failure_category=%s, failure_detail=%s,
                finished_at=current_timestamp
            where id=%s
            """,
            (category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message='boom' where job_id=%s and node_key=%s",
            (job_id, node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job_id,))
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
        _fail_node(app, job_id, "write_script", "technical", "provider_stream")
        _fail_node(app, job_id, "publish_content", "business", "review_rejected")

        all_runs = c.get(f"/api/workspaces/{ws_id}/failed-node-runs")
        technical = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?category=technical")
        by_detail = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?detail=review_rejected")

    assert all_runs.status_code == 200
    assert {r["node_key"] for r in all_runs.json()["runs"]} == {
        "write_script",
        "publish_content",
    }
    assert technical.status_code == 200
    technical_runs = technical.json()["runs"]
    assert len(technical_runs) == 1
    assert technical_runs[0]["job_id"] == job_id
    assert technical_runs[0]["node_key"] == "write_script"
    assert technical_runs[0]["failure_category"] == "technical"
    assert technical_runs[0]["failure_detail"] == "provider_stream"
    assert technical_runs[0]["error_message"] == "boom"
    assert by_detail.json()["runs"][0]["node_key"] == "publish_content"


def test_list_failed_node_runs_absent_and_explicit_workflow_key_are_equivalent(tmp_path):
    """#211 Phase 2 第二批：失败过滤的 workflow_key 缺省=从 path 推导。

    显式传（值=workspace id）与缺省返回同一集合；不匹配的 key 才收窄。
    """
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q920")
        _fail_node(app, job_id, "write_script", "technical", "provider_stream")

        absent = c.get(f"/api/workspaces/{ws_id}/failed-node-runs")
        explicit = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?workflow_key={ws_id}")
        mismatched = c.get(f"/api/workspaces/{ws_id}/failed-node-runs?workflow_key=other_wf")

    assert absent.status_code == 200
    assert explicit.status_code == 200
    assert {r["node_key"] for r in absent.json()["runs"]} == {"write_script"}
    assert explicit.json()["runs"] == absent.json()["runs"]
    assert mismatched.json()["runs"] == []


def test_rerun_by_failure_absent_and_explicit_workflow_key_are_equivalent(tmp_path):
    """失败重跑 POST body 的 workflow_key：缺省=从 path 推导，显式传照旧。

    两次调用各选各自的 job（job_ids 显式圈定），两条路径返回等价结果。
    """
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        absent_job = _create_job(c, ws_id, "Q921")
        _fail_node(app, absent_job, "write_script", "technical", "provider_stream")

        absent = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "technical", "job_ids": [absent_job]},
        )

        # 显式传：另一个失败 job，同一 workspace（key 值=id）。
        explicit_job = _create_job(c, ws_id, "Q922")
        _fail_node(app, explicit_job, "write_script", "technical", "provider_stream")
        explicit = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={
                "category": "technical",
                "job_ids": [explicit_job],
                "workflow_key": ws_id,
            },
        )

    assert absent.status_code == 200
    assert explicit.status_code == 200
    assert [(r["job_id"], r["status"]) for r in absent.json()["results"]] == [
        (absent_job, "succeeded")
    ]
    assert [(r["job_id"], r["status"]) for r in explicit.json()["results"]] == [
        (explicit_job, "succeeded")
    ]


def test_rerun_by_failure_route_reruns_matching_jobs(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q902")
        _fail_node(app, job_id, "review_script", "business", "review_rejected")

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
    assert results[0]["rerun_nodes"] == ["write_script"]
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"


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


def test_rerun_by_failure_from_node_key_overrides_strategy_target(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q910")
        _fail_node(app, job_id, "publish_content", "business", "review_rejected")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "business", "from_node_key": "publish_content"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["publish_content"]
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["publish_content"] == "pending"


def test_rerun_by_failure_from_node_key_upstream_of_failure(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q911")
        _fail_node(app, job_id, "publish_content", "business", "review_rejected")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "business", "from_node_key": "write_script"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["write_script"]
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["write_script"] == "pending"
    assert nodes["publish_content"] == "stale"


def test_rerun_by_failure_from_node_key_not_upstream_skips_job(tmp_path):
    from fastapi.testclient import TestClient

    app = _app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id, "Q912")
        _fail_node(app, job_id, "write_script", "technical", "provider_stream")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={
                "category": "technical",
                "job_ids": [job_id],
                "from_node_key": "publish_content",
            },
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "skipped"
    assert results[0]["reason_code"] == "no_matching_failure"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["write_script"] == "failed"
