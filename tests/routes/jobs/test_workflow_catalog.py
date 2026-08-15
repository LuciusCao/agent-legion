from tests.helpers.auth import authenticate_client


def test_get_workflow_definition_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workflows/question_comprehension_info")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow"]["key"] == "question_comprehension_info"
    assert body["workflow"]["label"] == "题目审题信息生成 DAG"
    assert body["workflow"]["intake"]["modes"] == [
        {
            "key": "batch_by_knowledge",
            "label": "按知识点批量",
            "input_field": "knowledge_codes",
        },
        {
            "key": "batch_by_ids",
            "label": "按题目ID批量",
            "input_field": "question_ids",
        },
    ]
    node_keys = [node["key"] for node in body["workflow"]["nodes"]]
    assert node_keys[0] == "fetch_questions"
    assert "assemble_comprehension_info" in node_keys
    assert all("label" in node for node in body["workflow"]["nodes"])
    fetch_node = next(
        node for node in body["workflow"]["nodes"] if node["key"] == "fetch_questions"
    )
    assert fetch_node["label"] == "获取题目"
    graph_node = next(
        node
        for node in body["workflow"]["nodes"]
        if node["key"] == "assess_comprehension_difficulty"
    )
    assert graph_node["after"] == ["review_key_info", "review_possible_errors"]


def test_list_workflows_includes_registered_workflows(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workflows")

    assert response.status_code == 200
    body = response.json()
    assert any(p["key"] for p in body["workflows"])


def test_update_workspace_rejects_invalid_workflow_key(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        create_resp = c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        assert create_resp.status_code == 200, create_resp.text
        ws_id = create_resp.json()["workspace"]["id"]
        resp = c.patch(
            f"/api/workspaces/{ws_id}",
            json={"default_workflow_key": "nonexistent"},
        )

    assert resp.status_code == 404
    assert "Unknown workflow" in resp.json()["detail"]


def test_register_workflow_via_admin_api(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.post(
            "/api/workflows",
            json={"key": "acme_quiz_flow", "label": "Acme Quiz", "description": "custom"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["key"] == "acme_quiz_flow"
        assert body["origin"] == "registered"

        listing = c.get("/api/workflows")
        assert listing.status_code == 200
        keys = {item["key"] for item in listing.json()["workflows"]}
        assert {"question_comprehension_info", "video_knowledge", "acme_quiz_flow"} <= keys

        # A registered key binds to new workspaces immediately.
        create_resp = c.post(
            "/api/workspaces",
            json={"name": "Acme", "default_workflow_key": "acme_quiz_flow"},
        )
        assert create_resp.status_code == 200, create_resp.text


def test_register_workflow_conflict_and_invalid_key(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        first = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Acme"})
        assert first.status_code == 200, first.text

        duplicate = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Again"})
        assert duplicate.status_code == 409

        builtin = c.post(
            "/api/workflows", json={"key": "question_comprehension_info", "label": "Hijack"}
        )
        assert builtin.status_code == 409

        invalid = c.post("/api/workflows", json={"key": "Has Space", "label": "Nope"})
        assert invalid.status_code == 400


def test_register_workflow_requires_authentication(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        response = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Acme"})

    assert response.status_code == 401


class _RecordingWorker:
    def __init__(self):
        self.reload_calls = 0

    def reload_scan_entries(self):
        self.reload_calls += 1


def test_register_workflow_triggers_scan_reload_and_wakeup(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    worker = _RecordingWorker()
    app.state.workflow_worker = worker
    wakeups = []
    monkeypatch.setattr(
        "server.app.routes.workflow_catalog_admin.notify_schedulable_work",
        lambda: wakeups.append(None),
    )
    with authenticate_client(TestClient(app)) as c:
        response = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Acme Quiz"})

    assert response.status_code == 200, response.text
    assert worker.reload_calls == 1
    assert len(wakeups) == 1


def test_register_workflow_failure_skips_scan_reload(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    worker = _RecordingWorker()
    app.state.workflow_worker = worker
    wakeups = []
    monkeypatch.setattr(
        "server.app.routes.workflow_catalog_admin.notify_schedulable_work",
        lambda: wakeups.append(None),
    )
    with authenticate_client(TestClient(app)) as c:
        first = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Acme"})
        assert first.status_code == 200, first.text
        assert worker.reload_calls == 1
        assert len(wakeups) == 1

        duplicate = c.post("/api/workflows", json={"key": "acme_quiz_flow", "label": "Again"})
        assert duplicate.status_code == 409
        invalid = c.post("/api/workflows", json={"key": "Has Space", "label": "Nope"})
        assert invalid.status_code == 400

    assert worker.reload_calls == 1
    assert len(wakeups) == 1


class _FailingReloadWorker:
    def reload_scan_entries(self):
        raise RuntimeError("catalog read failed")


def test_register_workflow_reload_failure_keeps_committed_write(tmp_path):
    """热刷新失败不得把已提交的注册写成 500：poll loop 周期对账自愈。"""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.workflow_worker = _FailingReloadWorker()
    with authenticate_client(TestClient(app)) as c:
        response = c.post("/api/workflows", json={"key": "acme_reload_flow", "label": "Acme"})

        assert response.status_code == 200, response.text
        assert response.json()["key"] == "acme_reload_flow"
        listing = c.get("/api/workflows")
        assert "acme_reload_flow" in {item["key"] for item in listing.json()["workflows"]}
