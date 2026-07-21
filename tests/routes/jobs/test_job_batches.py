import json
from concurrent.futures import ThreadPoolExecutor


def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def test_create_question_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert body["jobs"][0]["workspace_id"] == ws_id
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q001", "Q002"]


def test_async_question_batch_returns_before_cms_and_consumes_in_chunks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionDetail
    from server.app.main import create_app
    from server.app.services.job_intake_queue import JobIntakeQueue

    calls: list[str] = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append(question_id)
        return CmsQuestionDetail(
            question_id=question_id,
            title=f"Title {question_id}",
            normalized={"stem": f"Stem {question_id}"},
            payload={"uuid": question_id},
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )
    monkeypatch.setattr("server.app.services.job_intake_queue.INTAKE_QUEUE_CHUNK_SIZE", 2)

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    intake_queue = JobIntakeQueue(app.state.job_db, app.state.settings, app.state.job_event_buffer)
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002", "Q003"],
                "knowledge_codes": [],
                "async_processing": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["batch"]["status"] == "queued"
        assert body["created_count"] == 0
        assert body["jobs"] == []
        assert calls == []

        claimed = app.state.job_db.claim_intake_batch()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        first = app.state.job_db.get_batch(body["batch"]["id"])
        assert first is not None
        assert first["status"] == "queued"
        assert first["created_count"] == 2
        assert {job["source_id"] for job in app.state.job_db.list_jobs(workspace_id=ws_id)} == {
            "Q001",
            "Q002",
        }

        claimed = app.state.job_db.claim_intake_batch()
        assert claimed is not None
        intake_queue._consume_chunk(claimed)
        completed = app.state.job_db.get_batch(body["batch"]["id"])
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["created_count"] == 3
        assert calls == ["Q001", "Q002", "Q003"]


def test_async_batch_claim_is_atomic_across_consumers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    monkeypatch.setattr(
        "server.app.services.job_intake_queue.JobIntakeQueue.consume_once",
        lambda self: False,
    )
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
                "async_processing": True,
            },
        )
        assert response.status_code == 200

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: app.state.job_db.claim_intake_batch(), range(2)))

    assert len([claim for claim in claims if claim is not None]) == 1


def test_workspace_job_batch_stores_normalized_source_payload(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", " Q002 ", "Q001", ""],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["question_ids"] == ["Q001", "Q002"]
    assert payload["knowledge_codes"] == []
    assert body["created_count"] == 2


def test_create_workspace_job_batch_from_knowledge_codes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionSummary
    from server.app.main import create_app

    calls = []

    def fake_list_questions_by_knowledge(code, api_url=None, token=None):
        calls.append({"code": code, "api_url": api_url, "token": token})
        return [
            CmsQuestionSummary("Q001", "题目一", {"uuid": "Q001"}),
            CmsQuestionSummary("Q002", "题目二", {"uuid": "Q002"}),
        ]

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_list_url": "https://cms.example/question/list?bank_version=v5&page_size=50",
    }
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001", "K001", " K002 "],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert [call["code"] for call in calls] == ["K001", "K002"]
    assert calls[0]["api_url"] == "https://cms.example/question/list?bank_version=v5&page_size=50"
    assert calls[0]["token"] == "token"
    assert payload["knowledge_codes"] == ["K001", "K002"]
    assert payload["question_ids"] == ["Q001", "Q002"]
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["题目一", "题目二"]


def test_create_workspace_job_batch_from_question_ids_uses_cms_title(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionDetail
    from server.app.main import create_app

    calls = []

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        calls.append({"question_id": question_id, "api_url": api_url, "token": token})
        return CmsQuestionDetail(
            question_id=question_id,
            title=f"知识点名称-{question_id}",
            normalized={"stem": f"stem-{question_id}"},
            payload={"uuid": question_id},
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_detail_url": "https://cms.example/question/detail",
    }
    with TestClient(app) as c:
        workspace = c.post(
            "/api/workspaces",
            json={
                "name": "Question Id Batch",
                "default_workflow_key": "question_comprehension_info",
                "intake_config": {"enabled_modes": ["batch_by_ids"]},
            },
        ).json()["workspace"]
        response = c.post(
            f"/api/workspaces/{workspace['id']}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["question_ids"] == ["Q001", "Q002"]
    assert payload["knowledge_codes"] == []
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["知识点名称-Q001", "知识点名称-Q002"]
    assert [c["title"] for c in payload["task_candidates"]] == [
        "知识点名称-Q001",
        "知识点名称-Q002",
    ]
    assert all(c["source"]["kind"] == "batch_by_ids" for c in payload["task_candidates"])


def test_create_workspace_job_batch_rejects_empty_question_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": [" ", ""],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one question_id is required"


def test_question_comprehension_info_batch_by_ids_creates_one_job_per_question(client, monkeypatch):
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

    ws_id = _create_workspace(client)
    response = client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q1", "Q2", "Q1"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert {job["source_id"] for job in body["jobs"]} == {"Q1", "Q2"}
    assert all(job["workflow_key"] == "question_comprehension_info" for job in body["jobs"])


def test_question_comprehension_info_batch_by_knowledge_resolves_questions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionSummary
    from server.app.main import create_app

    calls = []

    def fake_list_questions_by_knowledge(code, api_url=None, token=None):
        calls.append({"code": code, "api_url": api_url, "token": token})
        return [
            CmsQuestionSummary("Q1", "题目一", {"uuid": "Q1"}),
            CmsQuestionSummary("Q2", "题目二", {"uuid": "Q2"}),
        ]

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_list_url": "https://cms.example/question/list?bank_version=v5&page_size=50",
    }
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001", "K001", " K002 "],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert [call["code"] for call in calls] == ["K001", "K002"]
    assert payload["knowledge_codes"] == ["K001", "K002"]
    assert payload["question_ids"] == ["Q1", "Q2"]
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["题目一", "题目二"]
    assert all(job["workflow_key"] == "question_comprehension_info" for job in body["jobs"])
