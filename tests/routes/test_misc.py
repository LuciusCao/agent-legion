from fastapi.testclient import TestClient

from tests.helpers import setup_spa_app


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
