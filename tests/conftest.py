from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.jobs import JobQueries
from server.app.main import create_app
from server.app.settings import load_settings
from tests.helpers import ensure_legacy_workspace_tables


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)


@pytest.fixture
def db(settings):
    return Database(settings.data_dir / "video_hive.sqlite")


@pytest.fixture
def job_db(settings):
    jobs_dir = settings.data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(settings.data_dir / "jobs.sqlite", jobs_dir)
    ensure_legacy_workspace_tables(queries)
    return queries


@pytest.fixture
def agent_manager():
    return AgentStatusManager()


@pytest.fixture
def app_factory(tmp_path):
    def factory(*, workflows_enabled=None, configure=None):
        app = create_app(data_dir=tmp_path, start_worker=False)
        if workflows_enabled is not None:
            app.state.settings.executor_runtime.workflows.enabled = workflows_enabled
        if configure is not None:
            configure(app)
        return app

    return factory


@pytest.fixture
def client_factory(app_factory):
    @contextmanager
    def factory(**app_options):
        app = app_factory(**app_options)
        with TestClient(app) as client:
            yield client

    return factory


@pytest.fixture
def client(client_factory):
    with client_factory() as c:
        yield c
