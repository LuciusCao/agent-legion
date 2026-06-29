from fastapi import FastAPI


def test_worker_pause_resume_api(client):
    status = client.get("/api/worker/status")
    assert status.status_code == 200
    assert status.json() == {"paused": True}
    assert client.app.state.worker_control.is_paused() is True

    paused = client.post("/api/worker/pause")
    assert paused.status_code == 200
    assert paused.json() == {"paused": True}
    assert client.app.state.worker_control.is_paused() is True

    resumed = client.post("/api/worker/resume")
    assert resumed.status_code == 200
    assert resumed.json() == {"paused": False}
    assert client.app.state.worker_control.is_paused() is False


def test_worker_tick_returns_accepted(client):
    response = client.post("/api/worker/tick")
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert client.app.state.worker_control.consume_tick() is True


def test_worker_status_with_workspace_defaults_to_paused(client):
    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.status_code == 200
    assert status.json() == {"paused": True}


def test_worker_pause_resume_with_workspace(client):
    paused = client.post("/api/worker/pause", params={"workspace_id": "ws-1"})
    assert paused.status_code == 200
    assert paused.json() == {"paused": True}
    assert client.app.state.workspace_worker_control.is_paused("ws-1") is True

    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.json() == {"paused": True}

    resumed = client.post("/api/worker/resume", params={"workspace_id": "ws-1"})
    assert resumed.status_code == 200
    assert resumed.json() == {"paused": False}
    assert client.app.state.workspace_worker_control.is_paused("ws-1") is False

    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.json() == {"paused": False}


def test_worker_with_video_hive_workspace_uses_workspace_worker(client):
    # workspace_id == "video-hive" is now treated like any other workspace.
    status = client.get("/api/worker/status", params={"workspace_id": "video-hive"})
    assert status.json() == {"paused": True}

    client.post("/api/worker/resume", params={"workspace_id": "video-hive"})
    assert client.app.state.workspace_worker_control.is_paused("video-hive") is False

    status = client.get("/api/worker/status", params={"workspace_id": "video-hive"})
    assert status.json() == {"paused": False}

    client.post("/api/worker/pause", params={"workspace_id": "video-hive"})
    assert client.app.state.workspace_worker_control.is_paused("video-hive") is True


def test_worker_without_workspace_control_defaults_to_paused():
    from fastapi.testclient import TestClient

    from server.app.routes.worker import create_worker_router
    from server.app.worker_control import WorkerControl

    worker_control = WorkerControl()
    worker_control.resume()
    router = create_worker_router(worker_control, workspace_worker_control=None)

    app = FastAPI()
    app.include_router(router, prefix="/api")

    with TestClient(app) as client:
        # Without workspace_worker_control, workspace-scoped endpoints default to paused
        # and do not mutate the global worker control.
        status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
        assert status.status_code == 200
        assert status.json() == {"paused": True}
        assert worker_control.is_paused() is False

        paused = client.post("/api/worker/pause", params={"workspace_id": "ws-1"})
        assert paused.json() == {"paused": True}
        assert worker_control.is_paused() is False

        resumed = client.post("/api/worker/resume", params={"workspace_id": "ws-1"})
        assert resumed.json() == {"paused": False}
        assert worker_control.is_paused() is False
