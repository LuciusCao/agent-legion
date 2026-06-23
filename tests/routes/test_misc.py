from fastapi.testclient import TestClient

from tests.helpers import setup_spa_app


def test_global_services_returns_200(client: TestClient):
    response = client.get("/api/global-services")
    assert response.status_code == 200
    data = response.json()
    assert "cms" in data
    assert "baseUrl" in data["cms"]


def test_app_ignores_partial_frontend_dist(tmp_path, monkeypatch):
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>partial build</div>", encoding="utf-8")

    app = main.create_app(data_dir=data_dir)
    with TestClient(app) as c:
        response = c.get("/")

    assert response.status_code == 200
    assert "Agent Legion API" in response.text


def test_video_hive_config_field_whitelist(client: TestClient):
    response = client.get("/api/video-hive/config")
    assert response.status_code == 200
    data = response.json()
    assert "asr" in data
    assert "provider" in data["asr"]
    assert isinstance(data["asr"]["whisperConfigured"], bool)
    assert "openclaw" in data
    assert "runnerCount" in data["openclaw"]
    # Ensure no local paths or secrets leak
    text = response.text.lower()
    forbidden = [
        "binary",
        "model",
        "script",
        "cwd",
        "command_template",
        "token",
        "secret",
        "nonce",
        "app_id",
        "path",
    ]
    for word in forbidden:
        assert word not in text, f"Field '{word}' should not appear in response"
