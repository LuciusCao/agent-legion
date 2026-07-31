"""Batch job operations selected by a server-side list filter."""

from server.app.storage_paths import resolve_job_dir

WORKFLOW_KEY = "question_comprehension_info"


def _create_workspace(client, name: str) -> str:
    response = client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": WORKFLOW_KEY}
    )
    assert response.status_code == 200
    return response.json()["workspace"]["id"]


def _create_jobs(client, workspace_id: str, question_ids: list[str]) -> list[str]:
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": WORKFLOW_KEY,
            "source_kind": "batch_by_ids",
            "question_ids": question_ids,
            "knowledge_codes": [],
        },
    )
    assert created.status_code == 200
    return [job["id"] for job in created.json()["jobs"]]


def _complete_job(client, job_id: str, question_id: str) -> None:
    job_db = client.app.state.job_db
    record = job_db.get_job(job_id)
    storage_dir = resolve_job_dir(record, client.app.state.settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "questions.json").write_text('{"question_id":"' + question_id + '"}')
    job_db.update_job_status(job_id, "completed")


def _fail_job_node(client, job_id: str, node_key: str, category: str, detail: str) -> None:
    job_db = client.app.state.job_db
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
            "update job_nodes set status='failed', error_message='boom'"
            " where job_id=? and node_key=?",
            (job_id, node_key),
        )
        conn.execute("update jobs set status='failed' where id=?", (job_id,))
        conn.execute("commit")


def _status(client, job_id: str) -> str:
    return client.app.state.job_db.get_job(job_id)["status"]


def test_batch_rerun_filter_selects_matching_jobs(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "rerun-filter-ws")
        failed_a, failed_b, completed = _create_jobs(client, ws_id, ["F1", "F2", "C1"])
        job_db = client.app.state.job_db
        job_db.update_job_status(failed_a, "failed", "boom")
        job_db.update_job_status(failed_b, "failed", "boom")
        job_db.update_job_status(completed, "completed")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"filter": {"status": "failed"}, "node_key": "fetch_questions"},
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert {r["job_id"] for r in results} == {failed_a, failed_b}
        assert all(r["status"] == "succeeded" for r in results)
        assert _status(client, failed_a) == "queued"
        assert _status(client, failed_b) == "queued"
        assert _status(client, completed) == "completed"


def test_batch_rerun_filter_honors_exclude_ids(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "rerun-exclude-ws")
        kept, excluded = _create_jobs(client, ws_id, ["F3", "F4"])
        job_db = client.app.state.job_db
        job_db.update_job_status(kept, "failed", "boom")
        job_db.update_job_status(excluded, "failed", "boom")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={
                "filter": {"status": "failed"},
                "exclude_ids": [excluded],
                "node_key": "fetch_questions",
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["job_id"] for r in results] == [kept]
        assert _status(client, kept) == "queued"
        assert _status(client, excluded) == "failed"


def test_batch_rerun_explicit_job_ids_still_work(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "rerun-explicit-ws")
        (job_id,) = _create_jobs(client, ws_id, ["F5"])
        client.app.state.job_db.update_job_status(job_id, "failed", "boom")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "fetch_questions"},
        )

        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "succeeded"
        assert _status(client, job_id) == "queued"


def test_batch_selection_requires_exactly_one_of_job_ids_or_filter(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "selection-validation-ws")
        (job_id,) = _create_jobs(client, ws_id, ["V1"])

        both = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={
                "job_ids": [job_id],
                "filter": {"status": "failed"},
                "node_key": "fetch_questions",
            },
        )
        neither = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"node_key": "fetch_questions"},
        )
        delete_neither = client.request("DELETE", f"/api/workspaces/{ws_id}/jobs/batch", json={})
        package_both = client.post(
            f"/api/workspaces/{ws_id}/jobs/package",
            json={"job_ids": [job_id], "filter": {"status": "completed"}},
        )
        rerun_by_failure_both = client.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={"category": "business", "job_ids": [job_id], "filter": {"status": "failed"}},
        )

        assert both.status_code == 422
        assert neither.status_code == 422
        assert delete_neither.status_code == 422
        assert package_both.status_code == 422
        assert rerun_by_failure_both.status_code == 422
        # The job is untouched by any of the rejected requests.
        assert client.app.state.job_db.get_job(job_id) is not None


def test_batch_delete_filter_and_exclude_ids(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "delete-filter-ws")
        excluded, deleted, pending = _create_jobs(client, ws_id, ["D1", "D2", "D3"])
        job_db = client.app.state.job_db
        job_db.update_job_status(excluded, "failed", "boom")
        job_db.update_job_status(deleted, "failed", "boom")

        response = client.request(
            "DELETE",
            f"/api/workspaces/{ws_id}/jobs/batch",
            json={"filter": {"status": "failed"}, "exclude_ids": [excluded]},
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["job_id"] for r in results] == [deleted]
        assert results[0]["status"] == "succeeded"
        assert job_db.get_job(deleted) is None
        assert job_db.get_job(excluded) is not None
        assert job_db.get_job(pending) is not None

        explicit = client.request(
            "DELETE",
            f"/api/workspaces/{ws_id}/jobs/batch",
            json={"job_ids": [excluded]},
        )
        assert explicit.status_code == 200
        assert job_db.get_job(excluded) is None
        assert job_db.get_job(pending) is not None


def test_batch_run_to_filter_selects_matching_jobs(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "run-to-filter-ws")
        pending, failed = _create_jobs(client, ws_id, ["R1", "R2"])
        job_db = client.app.state.job_db
        job_db.update_job_status(failed, "failed", "boom")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/batch-run-to",
            json={"filter": {"status": "pending"}, "target_node_key": "review_key_info"},
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["job_id"] for r in results] == [pending]
        assert results[0]["status"] == "succeeded"
        detail = client.get(f"/api/jobs/{pending}").json()
        assert detail["job"]["execution_control"]["target_node_key"] == "review_key_info"
        untouched = client.get(f"/api/jobs/{failed}").json()
        assert untouched["job"]["execution_control"]["mode"] == "full"


def test_package_filter_selects_matching_jobs(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "package-filter-ws")
        done_a, done_b, failed = _create_jobs(client, ws_id, ["P1", "P2", "P3"])
        job_db = client.app.state.job_db
        _complete_job(client, done_a, "P1")
        _complete_job(client, done_b, "P2")
        job_db.update_job_status(failed, "failed", "boom")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/package",
            json={"filter": {"status": "completed"}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["succeeded_count"] == 2
        assert body["failed_count"] == 0
        assert body["package_filename"]
        assert {r["job_id"] for r in body["results"]} == {done_a, done_b}
        assert job_db.get_job(done_a)["packed"] == 1
        assert job_db.get_job(done_b)["packed"] == 1
        assert job_db.get_job(failed)["packed"] == 0


def test_package_filter_empty_match_returns_400(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "package-empty-ws")
        _create_jobs(client, ws_id, ["P4"])

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/package",
            json={"filter": {"status": "cancelled"}},
        )

        assert response.status_code == 400
        assert "job_ids" in response.json()["detail"].lower()


def test_clear_packed_filter_selects_matching_jobs(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "clear-packed-filter-ws")
        packed_a, packed_b, unpacked = _create_jobs(client, ws_id, ["X1", "X2", "X3"])
        job_db = client.app.state.job_db
        job_db.set_jobs_packed([packed_a, packed_b], packed=1)

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/clear-packed",
            json={"filter": {"packed": 1}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["succeeded_count"] == 2
        assert {r["job_id"] for r in body["results"]} == {packed_a, packed_b}
        assert job_db.get_job(packed_a)["packed"] == 0
        assert job_db.get_job(packed_b)["packed"] == 0
        assert job_db.get_job(unpacked)["packed"] == 0


def test_clear_packed_filter_empty_match_returns_400(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "clear-packed-empty-ws")
        _create_jobs(client, ws_id, ["X4"])

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/clear-packed",
            json={"filter": {"packed": 1}},
        )

        assert response.status_code == 400
        assert "job_ids" in response.json()["detail"].lower()


def test_rerun_by_failure_filter_and_exclude_ids(client_factory):
    with client_factory(workflows_enabled=True) as client:
        ws_id = _create_workspace(client, "rerun-by-failure-filter-ws")
        job_a, job_b, job_c = _create_jobs(client, ws_id, ["B1", "B2", "B3"])
        for job_id in (job_a, job_b, job_c):
            _fail_job_node(client, job_id, "review_key_info", "business", "review_rejected")
        # job_c has a matching failed run but falls outside the status filter.
        client.app.state.job_db.update_job_status(job_c, "completed")

        response = client.post(
            f"/api/workspaces/{ws_id}/jobs/rerun-by-failure",
            json={
                "category": "business",
                "filter": {"status": "failed"},
                "exclude_ids": [job_b],
            },
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["job_id"] for r in results] == [job_a]
        assert results[0]["status"] == "succeeded"
        assert results[0]["rerun_nodes"] == ["generate_key_info"]
        nodes = {
            n["node_key"]: n["status"] for n in client.get(f"/api/jobs/{job_a}").json()["nodes"]
        }
        assert nodes["generate_key_info"] == "pending"
