from fastapi.testclient import TestClient

from server.app.agents import AgentStatusManager
from server.app.executors.registry import ExecutorRegistry
from server.app.pipeline.runners import RunnerPool
from server.app.worker_thread import WorkerThread
from server.app.workflow_worker_thread import WorkflowWorkerThread
from tests.helpers import setup_spa_app


def test_lifespan_with_start_worker_initializes_worker_threads(tmp_path, monkeypatch):
    from server.app import main

    calls = []
    received_registry = None

    def patched_worker_start(self):
        calls.append("worker")

    def patched_workflow_start(self):
        calls.append("workflow")
        nonlocal received_registry
        received_registry = self.executor_registry

    monkeypatch.setattr(WorkerThread, "start", patched_worker_start)
    monkeypatch.setattr(WorkflowWorkerThread, "start", patched_workflow_start)

    # Keep lifespan wiring independent of real openclaw discovery/runner creation.
    monkeypatch.setattr(AgentStatusManager, "discover", lambda self: [])
    monkeypatch.setattr(
        RunnerPool,
        "from_settings",
        classmethod(
            lambda cls, settings, discovered_agent_ids=None, agent_manager=None: RunnerPool(
                runners=[], agent_manager=agent_manager
            )
        ),
    )
    # Startup validation is about real runtime dependencies, not lifespan wiring.
    monkeypatch.setattr(main, "validate_settings", lambda settings: None)

    # Ensure required data dirs exist.
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (tmp_path / path_name).mkdir(parents=True, exist_ok=True)

    app = main.create_app(data_dir=tmp_path, start_worker=True)
    with TestClient(app) as _:
        pass  # lifespan startup runs here

    assert "worker" in calls
    assert "workflow" in calls
    assert isinstance(app.state.executor_registry, ExecutorRegistry)
    assert app.state.executor_registry is received_registry


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
