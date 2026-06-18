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
