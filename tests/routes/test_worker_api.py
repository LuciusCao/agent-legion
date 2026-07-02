from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.routes.worker import create_worker_router
from server.app.worker_control import WorkspaceWorkerControl


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
    # workspace_id == "video-hive" is treated like any other workspace.
    status = client.get("/api/worker/status", params={"workspace_id": "video-hive"})
    assert status.json() == {"paused": True}

    client.post("/api/worker/resume", params={"workspace_id": "video-hive"})
    assert client.app.state.workspace_worker_control.is_paused("video-hive") is False

    status = client.get("/api/worker/status", params={"workspace_id": "video-hive"})
    assert status.json() == {"paused": False}

    client.post("/api/worker/pause", params={"workspace_id": "video-hive"})
    assert client.app.state.workspace_worker_control.is_paused("video-hive") is True


def test_worker_without_workspace_id_is_required():
    workspace_worker_control = WorkspaceWorkerControl()
    workspace_worker_control.resume("ws-1")
    router = create_worker_router(workspace_worker_control)

    app = FastAPI()
    app.include_router(router, prefix="/api")

    with TestClient(app) as client:
        status = client.get("/api/worker/status")
        assert status.status_code == 422

        paused = client.post("/api/worker/pause")
        assert paused.status_code == 422

        resumed = client.post("/api/worker/resume")
        assert resumed.status_code == 422
