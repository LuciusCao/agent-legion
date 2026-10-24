"""Uncapped batch selections retain empty and chunked-read semantics (#1362)."""

from server.app.jobs.queries.job_bulk_sql import CHUNK_ROWS, id_chunks
from server.app.routes.job_batch_filter_contracts import JobSelectionMixin
from server.app.routes.job_rerun_by_failure_contracts import JobRerunByFailureRequest
from tests.helpers import publish_legacy_intake_revision

WORKFLOW_KEY = "education_video_problems_generation"
NODE_KEY = "intake_knowledge_points"
RETIRED_LIMIT = 20000


def _create_workspace(client, name: str) -> str:
    response = client.post("/api/workspaces", json={"id": WORKFLOW_KEY, "name": name})
    assert response.status_code == 200
    workspace_id = response.json()["workspace"]["id"]
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def _create_jobs(client, workspace_id: str, question_ids: list[str]) -> list[str]:
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": WORKFLOW_KEY,
            "source_kind": "direct_ids",
            "knowledge_point_ids": question_ids,
        },
    )
    assert created.status_code == 200
    return [job["id"] for job in created.json()["jobs"]]


def _fail_job_node(client, job_id: str, node_key: str) -> None:
    job_db = client.app.state.job_db
    run = job_db.start_node_run(job_id, node_key, ["cmd"], f"logs/jobs/{job_id}-{node_key}.log")
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set status='failed', error_message='boom',"
            " failure_category='business', failure_detail='rejected',"
            " finished_at=current_timestamp where id=%s",
            (run["id"],),
        )
        conn.execute(
            "update job_nodes set status='failed' where job_id=%s and node_key=%s",
            (job_id, node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job_id,))


def _missing_ids(count: int) -> list[str]:
    return [f"missing-{index:05d}" for index in range(count)]


def test_request_contracts_have_no_retired_max_length() -> None:
    ids = _missing_ids(RETIRED_LIMIT + 1)
    selection = JobSelectionMixin(job_ids=ids, exclude_ids=ids)
    failure = JobRerunByFailureRequest(category="business", job_ids=ids, exclude_ids=ids)
    assert len(selection.job_ids or []) == len(ids)
    assert len(selection.exclude_ids) == len(ids)
    assert len(failure.job_ids) == len(ids)
    assert len(failure.exclude_ids) == len(ids)


def test_id_chunks_bounds_and_dedupes() -> None:
    ids = [f"j{index}" for index in range(CHUNK_ROWS * 2 + 1)]
    chunks = list(id_chunks([*ids, ids[0], ids[-1]]))
    assert [len(chunk) for chunk in chunks] == [CHUNK_ROWS, CHUNK_ROWS, 1]
    assert [value for chunk in chunks for value in chunk] == ids
    assert list(id_chunks([])) == []


def test_explicit_job_ids_empty_list_is_a_noop(client) -> None:
    ws_id = _create_workspace(client, "bounds-empty-ws")

    rerun = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun",
        json={"job_ids": [], "node_key": NODE_KEY},
    )
    preview = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
        json={"job_ids": [], "node_key": NODE_KEY},
    )

    assert rerun.status_code == 200
    assert rerun.json()["results"] == []
    assert preview.json() == {"total_count": 0, "eligible_count": 0}


def test_explicit_ids_over_retired_limit_are_accepted(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD",
        RETIRED_LIMIT + 1,
    )
    ws_id = _create_workspace(client, "bounds-retired-limit-ws")
    (real_job,) = _create_jobs(client, ws_id, ["L1"])
    ids = [*_missing_ids(RETIRED_LIMIT), real_job]

    preview = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
        json={"job_ids": ids, "node_key": NODE_KEY},
    )
    rerun = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun",
        json={"job_ids": ids, "node_key": NODE_KEY},
    )

    assert preview.status_code == 200
    assert preview.json() == {"total_count": RETIRED_LIMIT + 1, "eligible_count": 1}
    assert rerun.status_code == 200
    results = rerun.json()["results"]
    assert len(results) == RETIRED_LIMIT + 1
    assert results[-1]["job_id"] == real_job
    assert results[-1]["status"] == "succeeded"
    assert {r["reason_code"] for r in results[:-1]} == {"not_found"}


def test_exclude_ids_over_retired_limit_are_accepted(client) -> None:
    ws_id = _create_workspace(client, "bounds-exclude-ws")
    response = client.request(
        "DELETE",
        f"/api/workspaces/{ws_id}/jobs/batch",
        json={"filter": {"status": "failed"}, "exclude_ids": _missing_ids(RETIRED_LIMIT + 1)},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_rerun_by_failure_unrestricted_has_no_cap(client) -> None:
    ws_id = _create_workspace(client, "bounds-by-failure-ws")
    job_ids = _create_jobs(client, ws_id, ["R1", "R2", "R3"])
    for job_id in job_ids:
        _fail_job_node(client, job_id, "review_script")

    response = client.post(
        f"/api/workspaces/{ws_id}/jobs/rerun-by-failure", json={"category": "business"}
    )

    assert response.status_code == 200, response.text
    assert sorted(r["job_id"] for r in response.json()["results"]) == sorted(job_ids)
