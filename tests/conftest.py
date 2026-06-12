import pytest
from fastapi.testclient import TestClient

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.jobs import JobQueries
from server.app.main import create_app
from server.app.settings import load_settings


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
    return JobQueries(settings.data_dir / "jobs.sqlite", jobs_dir)


@pytest.fixture
def agent_manager():
    return AgentStatusManager()


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as c:
        yield c
