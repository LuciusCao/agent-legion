from fastapi.testclient import TestClient

from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.settings import Settings
from server.app.worker_thread import WorkerThread


def test_lifespan_with_start_worker_initializes_worker_threads(tmp_path, monkeypatch):
    from server.app import main

    calls = []

    def patched_worker_start(self):
        calls.append("worker")

    def patched_pipeline_start(self):
        calls.append("pipeline")

    monkeypatch.setattr(WorkerThread, "start", patched_worker_start)
    monkeypatch.setattr(PipelineWorkerThread, "start", patched_pipeline_start)

    # Ensure required data dirs exist.
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (tmp_path / path_name).mkdir(parents=True, exist_ok=True)

    app = main.create_app(data_dir=tmp_path, start_worker=True)
    with TestClient(app) as _:
        pass  # lifespan startup runs here

    assert "worker" in calls
    assert "pipeline" in calls


def test_spa_catch_all_serves_static_files_and_fallback(tmp_path, monkeypatch):
    from server.app import main

    root_dir = tmp_path / "project"
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>spa-index</div>", encoding="utf-8")
    (frontend_dist / "vite.svg").write_text("<svg/>", encoding="utf-8")
    (frontend_assets / "main.js").write_text("console.log('main')", encoding="utf-8")

    data_dir = tmp_path / "data"
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (data_dir / path_name).mkdir(parents=True, exist_ok=True)

    def fake_load_settings(data_dir=None):
        resolved_data_dir = data_dir or tmp_path / "data"
        return Settings(
            root_dir=root_dir,
            data_dir=resolved_data_dir,
            videos_dir=resolved_data_dir / "videos",
            logs_dir=resolved_data_dir / "logs",
            packages_dir=resolved_data_dir / "packages",
            jobs_dir=resolved_data_dir / "jobs",
            config={},
        )

    monkeypatch.setattr(main, "load_settings", fake_load_settings)

    app = main.create_app(data_dir=data_dir, start_worker=False)
    with TestClient(app) as c:
        index = c.get("/")
        assert index.status_code == 200
        assert index.text == "<div>spa-index</div>"

        asset = c.get("/vite.svg")
        assert asset.status_code == 200
        assert asset.text == "<svg/>"

        unknown = c.get("/some/deep/client/route")
        assert unknown.status_code == 200
        assert unknown.text == "<div>spa-index</div>"

        mounted = c.get("/assets/main.js")
        assert mounted.status_code == 200
        assert mounted.text == "console.log('main')"


def test_spa_fallback_with_partial_build_shows_api_message(tmp_path, monkeypatch):
    from server.app import main

    root_dir = tmp_path / "project"
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    # Only index.html exists; no assets directory.
    (frontend_dist / "index.html").write_text("<div>partial</div>", encoding="utf-8")

    data_dir = tmp_path / "data"
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (data_dir / path_name).mkdir(parents=True, exist_ok=True)

    def fake_load_settings(data_dir=None):
        resolved_data_dir = data_dir or tmp_path / "data"
        return Settings(
            root_dir=root_dir,
            data_dir=resolved_data_dir,
            videos_dir=resolved_data_dir / "videos",
            logs_dir=resolved_data_dir / "logs",
            packages_dir=resolved_data_dir / "packages",
            jobs_dir=resolved_data_dir / "jobs",
            config={},
        )

    monkeypatch.setattr(main, "load_settings", fake_load_settings)

    app = main.create_app(data_dir=data_dir, start_worker=False)
    with TestClient(app) as c:
        response = c.get("/")
        assert response.status_code == 200
        assert "Video Hive API" in response.text
