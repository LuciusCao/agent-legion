import pytest
from fastapi.testclient import TestClient

from server.app.db import Database
from server.app.main import create_app
from server.app.settings import load_settings


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)


@pytest.fixture
def db(settings):
    return Database(settings.data_dir / "video_hive.sqlite")


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as c:
        yield c
