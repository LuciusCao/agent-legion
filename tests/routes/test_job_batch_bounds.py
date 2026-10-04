"""Batch selection bounds (#712 / #917 B-2): explicit list caps, filter caps,
and chunked prefetch reads."""

import pytest

from server.app.jobs.queries.job_bulk_sql import CHUNK_ROWS, id_chunks
from server.app.services import job_selection_resolver
from server.app.services.job_selection_resolver import MAX_BATCH_JOBS
from tests.helpers import publish_legacy_intake_revision

WORKFLOW_KEY = "education_video_problems_generation"
NODE_KEY = "intake_knowledge_points"


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
        conn.execute("commit")


def _missing_ids(count: int) -> list[str]:
    return [f"missing-{index:05d}" for index in range(count)]


def _assert_too_large(response) -> None:
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "batch_selection_too_large"
    assert detail["limit"] == job_selection_resolver.MAX_BATCH_JOBS
    assert "narrow the filter" in detail["message"]


@pytest.mark.no_db
def test_max_batch_jobs_aligns_with_bulk_chunking():
    assert MAX_BATCH_JOBS % CHUNK_ROWS == 0
    assert CHUNK_ROWS <= MAX_BATCH_JOBS <= 10 * CHUNK_ROWS


@pytest.mark.no_db
def test_id_chunks_bounds_and_dedupes():
    ids = [f"j{index}" for index in range(CHUNK_ROWS * 2 + 1)]
    chunks = list(id_chunks([*ids, ids[0], ids[-1]]))
    assert [len(chunk) for chunk in chunks] == [CHUNK_ROWS, CHUNK_ROWS, 1]
    assert [value for chunk in chunks for value in chunk] == ids
    assert list(id_chunks([])) == []


def test_explicit_job_ids_empty_list_is_a_noop(client):
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


def test_explicit_job_ids_exactly_at_limit_are_accepted(client):
    ws_id = _create_workspace(client, "bounds-at-limit-ws")
    (real_job,) = _create_jobs(client, ws_id, ["L1"])
    # The real job sits in the last prefetch chunk: chunked reads must still
    # find it among MAX_BATCH_JOBS ids.
    ids = [*_missing_ids(MAX_BATCH_JOBS - 1), real_job]

    preview = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
        json={"job_ids": ids, "node_key": NODE_KEY},
    )
    rerun = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun",
        json={"job_ids": ids, "node_key": NODE_KEY},
    )

    assert preview.status_code == 200
    assert preview.json() == {"total_count": MAX_BATCH_JOBS, "eligible_count": 1}
    assert rerun.status_code == 200
    results = rerun.json()["results"]
    assert len(results) == MAX_BATCH_JOBS
    assert results[-1]["job_id"] == real_job
    assert results[-1]["status"] == "succeeded"
    assert {r["reason_code"] for r in results[:-1]} == {"not_found"}


@pytest.mark.parametrize(
    ("method", "path", "extra"),
    [
        ("POST", "batch-rerun", {"node_key": NODE_KEY}),
        ("POST", "batch-rerun/preview", {"node_key": NODE_KEY}),
        ("DELETE", "batch", {}),
        ("POST", "batch-pause", {}),
        ("POST", "batch-upgrade-workflow", {}),
        ("POST", "package", {}),
        ("POST", "rerun-by-failure", {"category": "business"}),
    ],
)
def test_explicit_job_ids_over_limit_rejected_422(client, method, path, extra):
    ws_id = _create_workspace(client, f"bounds-over-{path.replace('/', '-')}-ws")
    (job_id,) = _create_jobs(client, ws_id, ["O1"])
    client.app.state.job_db.update_job_status(job_id, "failed", "boom")

    response = client.request(
        method,
        f"/api/workspaces/{ws_id}/jobs/{path}",
        json={"job_ids": [job_id, *_missing_ids(MAX_BATCH_JOBS)], **extra},
    )

    assert response.status_code == 422
    assert client.app.state.job_db.get_job(job_id)["status"] == "failed"


def test_exclude_ids_over_limit_rejected_422(client):
    ws_id = _create_workspace(client, "bounds-exclude-ws")

    response = client.request(
        "DELETE",
        f"/api/workspaces/{ws_id}/jobs/batch",
        json={"filter": {"status": "failed"}, "exclude_ids": _missing_ids(MAX_BATCH_JOBS + 1)},
    )

    assert response.status_code == 422


def test_filter_selection_over_limit_rejected_before_any_write(client, monkeypatch):
    monkeypatch.setattr(job_selection_resolver, "MAX_BATCH_JOBS", 2)
    ws_id = _create_workspace(client, "bounds-filter-ws")
    job_ids = _create_jobs(client, ws_id, ["F1", "F2", "F3"])
    job_db = client.app.state.job_db
    for job_id in job_ids:
        job_db.update_job_status(job_id, "failed", "boom")

    delete = client.request(
        "DELETE", f"/api/workspaces/{ws_id}/jobs/batch", json={"filter": {"status": "failed"}}
    )
    rerun = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun",
        json={"filter": {"status": "failed"}, "node_key": NODE_KEY},
    )
    preview = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
        json={"filter": {"status": "failed"}, "node_key": NODE_KEY},
    )
    upgrade = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
        json={"filter": {"status": "failed"}},
    )

    for response in (delete, rerun, preview, upgrade):
        _assert_too_large(response)
    assert [job_db.get_job(job_id)["status"] for job_id in job_ids] == ["failed"] * 3

    # Exactly at the limit (one excluded) the same filter goes through.
    within = client.post(
        f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
        json={"filter": {"status": "failed"}, "exclude_ids": [job_ids[0]], "node_key": NODE_KEY},
    )
    assert within.status_code == 200
    assert within.json()["total_count"] == 2


def test_rerun_by_failure_unrestricted_match_over_limit_rejected(client, monkeypatch):
    monkeypatch.setattr(job_selection_resolver, "MAX_BATCH_JOBS", 1)
    ws_id = _create_workspace(client, "bounds-by-failure-ws")
    job_ids = _create_jobs(client, ws_id, ["R1", "R2"])
    for job_id in job_ids:
        _fail_job_node(client, job_id, "review_script")

    response = client.post(
        f"/api/workspaces/{ws_id}/jobs/rerun-by-failure", json={"category": "business"}
    )

    _assert_too_large(response)
    job_db = client.app.state.job_db
    assert [job_db.get_job(job_id)["status"] for job_id in job_ids] == ["failed", "failed"]
