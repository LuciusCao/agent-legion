import json
from concurrent.futures import ThreadPoolExecutor

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


def test_create_question_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", "Q002"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert body["jobs"][0]["workspace_id"] == ws_id
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q001", "Q002"]


def test_async_batch_returns_queued_and_consumes_in_chunks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from server.app.services.job_intake_queue import JobIntakeQueue

    monkeypatch.setattr("server.app.services.job_intake_queue.INTAKE_QUEUE_CHUNK_SIZE", 2)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    intake_queue = JobIntakeQueue(app.state.job_db, app.state.settings, app.state.job_event_buffer)
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", "Q002", "Q003"],
                "async_processing": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["batch"]["status"] == "queued"
        assert body["created_count"] == 0
        assert body["jobs"] == []

        claimed = app.state.job_db.claim_intake_run()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        first = app.state.job_db.get_run(body["batch"]["id"])
        assert first is not None
        assert first["status"] == "queued"
        assert first["created_count"] == 2
        assert {job["source_id"] for job in app.state.job_db.list_jobs(workspace_id=ws_id)} == {
            "Q001",
            "Q002",
        }

        claimed = app.state.job_db.claim_intake_run()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        completed = app.state.job_db.get_run(body["batch"]["id"])
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["created_count"] == 3


def test_async_batch_claim_is_atomic_across_consumers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001"],
                "async_processing": True,
            },
        )
        assert response.status_code == 200

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: app.state.job_db.claim_intake_run(), range(2)))

    assert len([claim for claim in claims if claim is not None]) == 1


def test_workspace_job_batch_stores_normalized_source_payload(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", " Q002 ", "Q001", ""],
            },
        )

    assert response.status_code == 200
    body = response.json()
    # Normalized (trimmed, deduped) candidates land one per job input.
    inputs = [json.loads(job["input_json"]) for job in body["jobs"]]
    assert [doc["external_id"] for doc in inputs] == ["Q001", "Q002"]
    assert body["created_count"] == 2


def test_create_workspace_job_batch_from_direct_ids_uses_opaque_title(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        workspace = c.post(
            "/api/workspaces",
            json={
                "id": "direct_id_batch",
                "name": "Direct Id Batch",
                "intake_config": {"enabled_modes": ["direct_ids"]},
            },
        ).json()["workspace"]
        publish_legacy_intake_revision(c.app.state.job_db, workspace["id"])
        response = c.post(
            f"/api/workspaces/{workspace['id']}/job-batches",
            json={
                "workflow_key": "direct_id_batch",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", "Q002"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["Question Q001", "Question Q002"]
    inputs = [json.loads(job["input_json"]) for job in body["jobs"]]
    assert [doc["title"] for doc in inputs] == [
        "Question Q001",
        "Question Q002",
    ]
    # The source kind survives on the run row (legacy display field).
    assert body["batch"]["source_kind"] == "direct_ids"


def test_create_workspace_job_batch_rejects_empty_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": [" ", ""],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one knowledge_point_id is required"


def test_direct_ids_batch_creates_one_job_per_value(client):
    ws_id = _create_workspace(client)
    response = client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "education_video_problems_generation",
            "source_kind": "direct_ids",
            "knowledge_point_ids": ["Q1", "Q2", "Q1"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert {job["source_id"] for job in body["jobs"]} == {"Q1", "Q2"}
    assert all(job["workflow_key"] == "education_video_problems_generation" for job in body["jobs"])


def test_async_batch_resubmit_after_job_deletion_requeues_and_rebuilds(tmp_path, monkeypatch):
    """Regression (issue #55): re-submitting identical async input after the
    batch's jobs were deleted must requeue the completed batch so the consumer
    rebuilds the missing jobs, instead of silently returning 200 with 0 jobs."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from server.app.services.job_intake_queue import JobIntakeQueue

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    intake_queue = JobIntakeQueue(app.state.job_db, app.state.settings, app.state.job_event_buffer)
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    payload = {
        "workflow_key": "education_video_problems_generation",
        "source_kind": "direct_ids",
        "knowledge_point_ids": ["Q001", "Q002"],
        "knowledge_codes": [],
        "async_processing": True,
    }
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(f"/api/workspaces/{ws_id}/job-batches", json=payload)
        assert response.status_code == 200
        batch_id = response.json()["batch"]["id"]

        claimed = app.state.job_db.claim_intake_run()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        completed = app.state.job_db.get_run(batch_id)
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["created_count"] == 2

        job_ids = [job["id"] for job in app.state.job_db.list_jobs(workspace_id=ws_id)]
        assert len(job_ids) == 2
        delete_response = c.request(
            "DELETE", f"/api/workspaces/{ws_id}/jobs/batch", json={"job_ids": job_ids}
        )
        assert delete_response.status_code == 200
        assert app.state.job_db.list_jobs(workspace_id=ws_id) == []

        resubmit = c.post(f"/api/workspaces/{ws_id}/job-batches", json=payload)
        assert resubmit.status_code == 200
        body = resubmit.json()
        assert body["batch"]["id"] == batch_id
        assert body["batch"]["status"] == "queued"
        assert body["created_count"] == 0
        # The guarded transition is a no-op once the batch is no longer
        # completed (already requeued, or claimed by the intake consumer).
        assert app.state.job_db.requeue_completed_run_if_depleted(batch_id, {}, 1) is None

        claimed = app.state.job_db.claim_intake_run()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        rebuilt = app.state.job_db.get_run(batch_id)
        assert rebuilt is not None
        assert rebuilt["status"] == "completed"
        assert rebuilt["created_count"] == 2
        assert {job["source_id"] for job in app.state.job_db.list_jobs(workspace_id=ws_id)} == {
            "Q001",
            "Q002",
        }


def test_async_batch_resubmit_without_deletion_keeps_idempotency(tmp_path, monkeypatch):
    """Re-submitting identical async input while the batch's jobs still exist
    must remain a no-op: the completed batch is not requeued and no duplicate
    jobs are created."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from server.app.services.job_intake_queue import JobIntakeQueue

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    intake_queue = JobIntakeQueue(app.state.job_db, app.state.settings, app.state.job_event_buffer)
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    payload = {
        "workflow_key": "education_video_problems_generation",
        "source_kind": "direct_ids",
        "knowledge_point_ids": ["Q001", "Q002"],
        "knowledge_codes": [],
        "async_processing": True,
    }
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(f"/api/workspaces/{ws_id}/job-batches", json=payload)
        assert response.status_code == 200
        batch_id = response.json()["batch"]["id"]

        claimed = app.state.job_db.claim_intake_run()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)

        resubmit = c.post(f"/api/workspaces/{ws_id}/job-batches", json=payload)
        assert resubmit.status_code == 200
        body = resubmit.json()
        assert body["batch"]["id"] == batch_id
        assert body["batch"]["status"] == "completed"
        assert body["created_count"] == 2
        assert app.state.job_db.claim_intake_run() is None
        assert len(app.state.job_db.list_jobs(workspace_id=ws_id)) == 2


def test_async_batch_chunk_failure_is_recorded_and_remaining_chunks_continue(tmp_path, monkeypatch):
    """Regression: one failing chunk must not terminally fail the whole async
    batch — the error is recorded, remaining values are still processed, and
    no jobs are duplicated."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from server.app.services.job_intake_queue import JobIntakeQueue
    from server.app.services.job_intake_registry import RESOLVERS
    from server.app.services.job_intake_resolution import (
        resolve_direct_candidates,
    )

    def flaky_resolver(entity, input_values, source_kind):
        if "Q002" in input_values:
            raise RuntimeError("resolver boom")
        return resolve_direct_candidates(entity, input_values, source_kind)

    spec = RESOLVERS[("question", "direct_ids")]
    monkeypatch.setitem(
        RESOLVERS, ("question", "direct_ids"), replace(spec, handler=flaky_resolver)
    )
    monkeypatch.setattr("server.app.services.job_intake_queue.INTAKE_QUEUE_CHUNK_SIZE", 1)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    intake_queue = JobIntakeQueue(app.state.job_db, app.state.settings, app.state.job_event_buffer)
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", "Q002", "Q003"],
                "async_processing": True,
            },
        )
        assert response.status_code == 200
        batch_id = response.json()["batch"]["id"]

        for _ in range(3):
            claimed = app.state.job_db.claim_intake_run()
            assert claimed is not None
            intake_queue._consume_chunk(claimed)

        completed = app.state.job_db.get_run(batch_id)
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["created_count"] == 2
        assert "resolver boom" in completed["error_message"]
        jobs = app.state.job_db.list_jobs(workspace_id=ws_id)
        assert sorted(job["source_id"] for job in jobs) == ["Q001", "Q003"]
