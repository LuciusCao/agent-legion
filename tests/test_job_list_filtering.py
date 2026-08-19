import pytest

from tests.helpers import publish_builtin_revision

WORKFLOW_KEY = "education_video_problems_generation"


def _make_workspace(job_db, slug):
    workspace = job_db.create_workspace(slug, default_workflow_key=WORKFLOW_KEY)
    publish_builtin_revision(job_db, workspace["id"])
    return workspace


def _make_job(job_db, workspace_id, source_id, title="", batch_id="", node_keys=("n1", "n2")):
    return job_db.create_job(
        workspace_id=workspace_id,
        workflow_key=WORKFLOW_KEY,
        source_type="question_id",
        source_id=source_id,
        batch_id=batch_id,
        title=title,
        node_keys=list(node_keys),
    )


def _execute(job_db, sql, params=()):
    with job_db.connect() as conn:
        conn.execute(sql, params)


def _set_status(job_db, job_id, status):
    _execute(job_db, "update jobs set status = %s where id = %s", (status, job_id))


def _set_node_status(job_db, job_id, node_key, status):
    _execute(
        job_db,
        "update job_nodes set status = %s where job_id = %s and node_key = %s",
        (status, job_id, node_key),
    )


def _snapshot(client, workspace_id, query=""):
    response = client.get(f"/api/workspaces/{workspace_id}/jobs/snapshot{query}")
    assert response.status_code == 200
    return response.json()


def _facets(client, workspace_id, query=""):
    response = client.get(f"/api/workspaces/{workspace_id}/jobs/facets{query}")
    assert response.status_code == 200
    return response.json()


def test_status_filter_folds_unknown_statuses_into_pending(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-status-ws")
        queued = _make_job(job_db, workspace["id"], "q-queued")
        running = _make_job(job_db, workspace["id"], "q-running")
        unknown = _make_job(job_db, workspace["id"], "q-unknown")
        _set_status(job_db, running["id"], "running")
        _set_status(job_db, unknown["id"], "mystery_state")

        pending = _snapshot(client, workspace["id"], "?status=pending")
        assert pending["total"] == 2
        assert {job["id"] for job in pending["jobs"]} == {queued["id"], unknown["id"]}

        running_data = _snapshot(client, workspace["id"], "?status=running")
        assert running_data["total"] == 1
        assert running_data["jobs"][0]["id"] == running["id"]


def test_search_filter_matches_across_fields_case_insensitively(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-search-ws")
        by_title = _make_job(job_db, workspace["id"], "q-title", title="Algebra Question")
        by_source = _make_job(job_db, workspace["id"], "q-SourceId")
        by_batch = _make_job(job_db, workspace["id"], "q-batch", batch_id="Batch-77")
        _make_job(job_db, workspace["id"], "q-other", title="Geometry")

        by_title_hit = _snapshot(client, workspace["id"], "?search=algebra")
        assert [job["id"] for job in by_title_hit["jobs"]] == [by_title["id"]]

        by_source_hit = _snapshot(client, workspace["id"], "?search=sourceid")
        assert [job["id"] for job in by_source_hit["jobs"]] == [by_source["id"]]

        by_batch_hit = _snapshot(client, workspace["id"], "?search=batch-77")
        assert [job["id"] for job in by_batch_hit["jobs"]] == [by_batch["id"]]

        by_id_hit = _snapshot(client, workspace["id"], "?search=q-other")
        assert len(by_id_hit["jobs"]) == 1

        trimmed = _snapshot(client, workspace["id"], "?search=  algebra  ")
        assert trimmed["total"] == 1


def test_search_filter_escapes_like_wildcards(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-escape-ws")
        percent = _make_job(job_db, workspace["id"], "q-percent", title="100% legit")
        _make_job(job_db, workspace["id"], "q-plain", title="1000x legit")

        data = _snapshot(client, workspace["id"], "?search=100%25")
        assert [job["id"] for job in data["jobs"]] == [percent["id"]]


def test_workflow_version_filters(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-version-ws")
        v1 = _make_job(job_db, workspace["id"], "q-v1")
        v2 = _make_job(job_db, workspace["id"], "q-v2")
        none_v = _make_job(job_db, workspace["id"], "q-vnone")
        _execute(job_db, "update jobs set workflow_version = 1 where id = %s", (v1["id"],))
        _execute(job_db, "update jobs set workflow_version = 2 where id = %s", (v2["id"],))
        _execute(job_db, "update jobs set workflow_version = null where id = %s", (none_v["id"],))

        by_version = _snapshot(client, workspace["id"], "?workflow_version=1")
        assert [job["id"] for job in by_version["jobs"]] == [v1["id"]]

        by_none = _snapshot(client, workspace["id"], "?workflow_version_none=true")
        assert [job["id"] for job in by_none["jobs"]] == [none_v["id"]]

        conflict = client.get(
            f"/api/workspaces/{workspace['id']}/jobs/snapshot"
            "?workflow_version=1&workflow_version_none=true"
        )
        assert conflict.status_code == 400


def test_active_node_key_prefers_running_then_first_failed(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-node-ws")
        failed_job = _make_job(job_db, workspace["id"], "q-failed")
        running_job = _make_job(job_db, workspace["id"], "q-running")
        # No running node: active node is the first failed node by id.
        _set_node_status(job_db, failed_job["id"], "n1", "failed")
        # A running node wins over an earlier failed node.
        _set_node_status(job_db, running_job["id"], "n1", "failed")
        _set_node_status(job_db, running_job["id"], "n2", "running")

        by_n1 = _snapshot(client, workspace["id"], "?active_node_key=n1")
        assert [job["id"] for job in by_n1["jobs"]] == [failed_job["id"]]

        by_n2 = _snapshot(client, workspace["id"], "?active_node_key=n2")
        assert [job["id"] for job in by_n2["jobs"]] == [running_job["id"]]


def test_packed_filter(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-packed-ws")
        packed = _make_job(job_db, workspace["id"], "q-packed")
        _make_job(job_db, workspace["id"], "q-unpacked")
        _execute(job_db, "update jobs set packed = 1 where id = %s", (packed["id"],))

        data = _snapshot(client, workspace["id"], "?packed=1")
        assert [job["id"] for job in data["jobs"]] == [packed["id"]]

        unpacked = _snapshot(client, workspace["id"], "?packed=0")
        assert unpacked["total"] == 1
        assert unpacked["jobs"][0]["id"] != packed["id"]


def test_filtered_pagination_returns_total_only_on_first_page(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "filter-page-ws")
        created = []
        for i in range(5):
            job = _make_job(job_db, workspace["id"], f"q-page-{i}")
            created.append(job["id"])
        for job_id in created[:2]:
            _set_status(job_db, job_id, "running")

        first = _snapshot(client, workspace["id"], "?status=pending&limit=2")
        assert first["total"] == 3
        assert first["stats"] == {"pending": 3, "running": 2}
        assert len(first["jobs"]) == 2
        assert first["next_cursor"] is not None

        second = _snapshot(
            client, workspace["id"], f"?status=pending&limit=2&cursor={first['next_cursor']}"
        )
        assert second["total"] is None
        assert second["stats"] == {}
        assert len(second["jobs"]) == 1
        assert second["next_cursor"] is None

        ids = [job["id"] for job in first["jobs"] + second["jobs"]]
        assert sorted(ids) == sorted(created[2:])


def test_facets_exclude_own_dimension(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "facets-ws")
        job_a = _make_job(job_db, workspace["id"], "q-a", title="alpha one")
        job_b = _make_job(job_db, workspace["id"], "q-b", title="alpha two")
        _make_job(job_db, workspace["id"], "q-c", title="beta")
        _set_status(job_db, job_b["id"], "running")
        _set_node_status(job_db, job_a["id"], "n1", "failed")
        _set_node_status(job_db, job_b["id"], "n2", "running")
        _execute(job_db, "update jobs set workflow_version = 1 where id = %s", (job_a["id"],))

        data = _facets(client, workspace["id"], "?search=alpha&status=pending")
        # total applies every filter.
        assert data["total"] == 1
        # status_counts ignores the status filter but applies the search.
        assert data["status_counts"] == {"pending": 1, "running": 1}
        # version_counts applies both search and status filters.
        assert data["version_counts"] == {"1": 1}
        # node_counts applies both search and status filters.
        assert data["node_counts"] == {"n1": 1}


def test_facets_null_keys_and_unfiltered_counts(client_factory):
    with client_factory(workflows_enabled=True) as client:
        job_db = client.app.state.job_db
        workspace = _make_workspace(job_db, "facets-null-ws")
        job_a = _make_job(job_db, workspace["id"], "q-a")
        _make_job(job_db, workspace["id"], "q-b")
        _set_node_status(job_db, job_a["id"], "n1", "failed")

        data = _facets(client, workspace["id"])
        assert data["total"] == 2
        assert data["status_counts"] == {"pending": 2}
        # Jobs without a workflow version are keyed "none".
        assert data["version_counts"] == {"none": 2}
        # Jobs without a running/failed node are keyed "".
        assert data["node_counts"] == {"n1": 1, "": 1}

        by_node = _facets(client, workspace["id"], "?active_node_key=n1")
        assert by_node["total"] == 1
        # node_counts ignores the active_node_key filter itself.
        assert by_node["node_counts"] == {"n1": 1, "": 1}


def test_facets_requires_workflows_enabled(client_factory):
    with client_factory(workflows_enabled=False) as client:
        response = client.get("/api/workspaces/any/jobs/facets")
    assert response.status_code == 404


@pytest.mark.parametrize("endpoint", ["snapshot", "facets"])
def test_filtered_endpoints_reject_conflicting_version_params(client_factory, endpoint):
    with client_factory(workflows_enabled=True) as client:
        response = client.get(
            f"/api/workspaces/any/jobs/{endpoint}?workflow_version=1&workflow_version_none=true"
        )
    assert response.status_code == 400
