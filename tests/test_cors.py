from fastapi.testclient import TestClient

from server.app.configuration.cors import load_cors_settings
from server.app.main import create_app
from server.app.settings import load_settings


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


def test_cors_env_overrides_drive_settings(tmp_path, monkeypatch):
    """server.cors is env-only after the app.yaml retirement."""
    monkeypatch.setenv(
        "AGENT_LEGION_CORS_ALLOW_ORIGINS",
        "https://admin.example, https://tools.example/",
    )
    monkeypatch.setenv("AGENT_LEGION_CORS_ALLOW_CREDENTIALS", "true")
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.cors.allow_origins == ("https://admin.example", "https://tools.example")
    assert settings.cors.allow_credentials is True


def test_cors_defaults_match_retired_yaml_values(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_LEGION_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("AGENT_LEGION_CORS_ALLOW_CREDENTIALS", raising=False)
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert settings.cors.allow_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert settings.cors.allow_credentials is False
