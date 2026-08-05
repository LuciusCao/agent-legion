from fastapi.testclient import TestClient

from server.app.events.agents import AgentStatusManager
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.sweeper import SweeperThread
from server.app.workflow_worker.thread import WorkflowWorkerThread
from tests.helpers import setup_spa_app


def test_lifespan_with_start_worker_initializes_only_workflow_worker(tmp_path, monkeypatch):
    from server.app import main

    calls = []
    received_registry = None

    def patched_workflow_start(self):
        calls.append("workflow")
        nonlocal received_registry
        received_registry = self.executor_registry

    monkeypatch.setattr(WorkflowWorkerThread, "start", patched_workflow_start)

    # Keep lifespan wiring independent of real openclaw discovery.
    monkeypatch.setattr(AgentStatusManager, "discover", lambda self: [])
    # Startup validation is about real runtime dependencies, not lifespan wiring.
    monkeypatch.setattr(main, "validate_settings", lambda settings: None)

    # Ensure required data dirs exist.
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (tmp_path / path_name).mkdir(parents=True, exist_ok=True)

    app = main.create_app(data_dir=tmp_path, start_worker=True)
    with TestClient(app) as _:
        pass  # lifespan startup runs here

    assert "workflow" in calls
    assert isinstance(app.state.executor_registry, ExecutorRegistry)
    assert app.state.executor_registry is received_registry


def test_lifespan_sweeper_disabled_by_settings(tmp_path, monkeypatch):
    from server.app import main

    calls = []

    def patched_workflow_start(self):
        calls.append("workflow")

    def patched_sweeper_start(self):
        calls.append("sweeper")

    monkeypatch.setattr(WorkflowWorkerThread, "start", patched_workflow_start)
    monkeypatch.setattr(SweeperThread, "start", patched_sweeper_start)
    monkeypatch.setattr(AgentStatusManager, "discover", lambda self: [])
    monkeypatch.setattr(main, "validate_settings", lambda settings: None)

    for path_name in ["videos", "logs", "packages", "jobs"]:
        (tmp_path / path_name).mkdir(parents=True, exist_ok=True)

    real_load_settings = main.load_settings

    def load_with_sweeper_disabled(**kwargs):
        settings = real_load_settings(**kwargs)
        settings.executor_runtime.sweeper_enabled = False
        return settings

    monkeypatch.setattr(main, "load_settings", load_with_sweeper_disabled)

    app = main.create_app(data_dir=tmp_path, start_worker=True)
    with TestClient(app) as _:
        pass  # lifespan startup runs here

    # sweeper_enabled=False hands lease hygiene to an external sweeper process.
    assert "workflow" in calls
    assert "sweeper" not in calls


def test_spa_catch_all_serves_static_files_and_fallback(tmp_path, monkeypatch):
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>spa-index</div>", encoding="utf-8")
    (frontend_dist / "vite.svg").write_text("<svg/>", encoding="utf-8")
    (frontend_assets / "main.js").write_text("console.log('main')", encoding="utf-8")

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


def test_spa_cache_headers(tmp_path, monkeypatch):
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>spa-index</div>", encoding="utf-8")
    (frontend_dist / "vite.svg").write_text("<svg/>", encoding="utf-8")
    (frontend_assets / "main.js").write_text("console.log('main')", encoding="utf-8")

    app = main.create_app(data_dir=data_dir, start_worker=False)
    with TestClient(app) as c:
        assert c.get("/").headers["Cache-Control"] == "no-cache"
        assert c.get("/some/client/route").headers["Cache-Control"] == "no-cache"
        assert c.get("/vite.svg").headers["Cache-Control"] == "no-cache"
        assert (
            c.get("/assets/main.js").headers["Cache-Control"]
            == "public, max-age=31536000, immutable"
        )


def test_gzip_compresses_text_but_skips_range_requests(tmp_path, monkeypatch):
    from server.app import main

    root_dir, data_dir = setup_spa_app(tmp_path, monkeypatch)
    frontend_dist = root_dir / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div>spa-index</div>", encoding="utf-8")
    big_js = "console.log('x')\n" * 100  # above the gzip minimum_size
    (frontend_assets / "big.js").write_text(big_js, encoding="utf-8")

    app = main.create_app(data_dir=data_dir, start_worker=False)
    with TestClient(app) as c:
        plain = c.get("/assets/big.js", headers={"Accept-Encoding": "identity"})
        assert plain.status_code == 200
        assert "content-encoding" not in plain.headers
        assert plain.text == big_js

        gzipped = c.get("/assets/big.js", headers={"Accept-Encoding": "gzip"})
        assert gzipped.status_code == 200
        assert gzipped.headers["content-encoding"] == "gzip"
        assert gzipped.text == big_js  # httpx decompresses transparently

        ranged = c.get(
            "/assets/big.js",
            headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
        )
        assert ranged.status_code == 206
        assert "content-encoding" not in ranged.headers
        assert ranged.headers["content-range"].startswith("bytes 0-99/")
