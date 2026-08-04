from fastapi.testclient import TestClient

from server.app.configuration.cors import load_cors_settings
from server.app.main import create_app


def test_default_cors_allows_local_vite_origin(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_default_cors_rejects_unlisted_origin(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_rejects_wildcard_with_credentials():
    config = {
        "server": {
            "cors": {
                "allow_origins": ["*"],
                "allow_credentials": True,
            }
        }
    }

    try:
        load_cors_settings(config)
    except ValueError as exc:
        assert str(exc) == "credentialed CORS cannot use a wildcard origin"
    else:
        raise AssertionError("expected invalid credentialed wildcard CORS to fail")
