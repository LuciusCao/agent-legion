from fastapi.testclient import TestClient

from server.app import main


def test_start_worker_starts_only_workflow_worker(tmp_path, monkeypatch) -> None:
    started = {"workflow": 0}

    def fake_workflow_start(self):
        started["workflow"] += 1

    monkeypatch.setattr(main.WorkflowWorkerThread, "start", fake_workflow_start)
    monkeypatch.setattr(
        main.WorkflowWorkerThread, "is_enabled", staticmethod(lambda settings: True)
    )
    monkeypatch.setattr(main.AgentStatusManager, "discover", lambda self: [])
    monkeypatch.setattr(main, "validate_settings", lambda settings: None)

    app = main.create_app(data_dir=tmp_path, start_worker=True)
    with TestClient(app):
        pass  # lifespan startup runs here

    assert app.title == "Agent Legion"
    assert started["workflow"] == 1
