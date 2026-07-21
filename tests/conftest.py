import json
import os
from contextlib import contextmanager
from urllib.parse import urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA

os.environ["VIDEO_HIVE_DATABASE_URL"] = TEST_DATABASE_URL

import psycopg
from psycopg import sql

with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as _bootstrap_conn:
    _bootstrap_conn.execute(
        sql.SQL("create schema if not exists {}").format(sql.Identifier(TEST_SCHEMA))
    )

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.db.connection import close_database_pools
from server.app.db.schema import init_db
from server.app.jobs import JobQueries
from server.app.main import create_app
from server.app.settings import load_settings

_CMS_ENV_KEYS = (
    "BASECMS_BASE_URL",
    "BASECMS_TOKEN",
    "BASECMS_APP_ID",
    "BASECMS_NONCE",
    "BASECMS_SECRET",
    "BASECMS_TOKEN_URL",
    "VIDEO_HIVE_CMS_TOKEN",
    "VIDEO_HIVE_CMS_TOKEN_GEN_SECRET",
)


def pytest_configure() -> None:
    if os.environ.get("VIDEO_HIVE_TEST_REAL_CMS") != "1":
        os.environ.setdefault("VIDEO_HIVE_SKIP_DOTENV", "1")
        for key in _CMS_ENV_KEYS:
            os.environ[key] = ""


@pytest.fixture(autouse=True)
def _isolate_postgres_database():
    close_database_pools()
    try:
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(TEST_SCHEMA))
            )
            conn.execute(sql.SQL("create schema {}").format(sql.Identifier(TEST_SCHEMA)))
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set VIDEO_HIVE_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    init_db(TEST_DATABASE_URL)
    yield
    close_database_pools()


@pytest.fixture(autouse=True)
def _isolate_project_dotenv(monkeypatch):
    """Keep unit tests from inheriting real local credentials by default.

    Production and local app runs still load the project .env normally. Tests
    that intentionally exercise real CMS credentials can opt in with
    VIDEO_HIVE_TEST_REAL_CMS=1.
    """
    if os.environ.get("VIDEO_HIVE_TEST_REAL_CMS") == "1":
        return
    monkeypatch.setenv("VIDEO_HIVE_SKIP_DOTENV", "1")
    for key in _CMS_ENV_KEYS:
        monkeypatch.setenv(key, "")


@pytest.fixture(autouse=True)
def _block_real_cms_http(monkeypatch):
    if os.environ.get("VIDEO_HIVE_TEST_REAL_CMS") == "1":
        return
    original_request = requests.sessions.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        host = urlparse(str(url)).hostname or ""
        if host == "cms.example.com" or host.endswith(".internal.example.com"):
            return _fake_cms_response(method, url, kwargs.get("params"))
        return original_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded_request)


def _fake_cms_response(method: str, url: object, params: object) -> requests.Response:
    if str(method).upper() != "GET":
        raise RuntimeError(
            f"Real CMS HTTP is disabled in tests; mock the CMS boundary instead: {url}"
        )
    parsed = urlparse(str(url))
    query_params = params if isinstance(params, dict) else {}
    question_id = str(query_params.get("uuid") or "Q001")
    knowledge_code = str(query_params.get("knowledge") or "K001")
    if parsed.path.endswith("/question/list"):
        payload = {
            "code": 0,
            "message": "success",
            "data": {
                "question_list": [
                    _fake_cms_question_item(f"{knowledge_code}-Q1"),
                    _fake_cms_question_item(f"{knowledge_code}-Q2"),
                ],
                "total": 2,
            },
        }
    else:
        payload = {"code": 0, "message": "success", "data": _fake_cms_question_item(question_id)}
    response = requests.Response()
    response.status_code = 200
    response.url = str(url)
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload).encode("utf-8")
    return response


def _fake_cms_question_item(question_id: str) -> dict[str, object]:
    return {
        "question_uuid": question_id,
        "question_title": question_id,
        "body": {"content": f"Stem for {question_id}"},
        "option": [],
        "answer": [],
        "analyze": [],
    }


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)


@pytest.fixture
def db(settings):
    return Database(TEST_DATABASE_URL)


@pytest.fixture
def job_db(settings):
    jobs_dir = settings.data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir)
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
