import json
import os
from contextlib import contextmanager
from urllib.parse import urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA

os.environ["AGENT_LEGION_DATABASE_URL"] = TEST_DATABASE_URL

import psycopg
from psycopg import sql

with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as _bootstrap_conn:
    _bootstrap_conn.execute(
        sql.SQL("create schema if not exists {}").format(sql.Identifier(TEST_SCHEMA))
    )

from server.app.agent_catalog import load_agent_definitions, sync_agent_definitions
from server.app.agents import AgentStatusManager
from server.app.configuration import load_application_config
from server.app.db.connection import close_database_pools
from server.app.db.schema import init_db
from server.app.jobs import JobQueries
from server.app.main import create_app
from server.app.settings import PROJECT_ROOT, load_settings

_CMS_ENV_KEYS = (
    "CMS_BASE_URL",
    "CMS_TOKEN",
    "CMS_APP_ID",
    "CMS_NONCE",
    "CMS_SECRET",
    "CMS_TOKEN_URL",
    "BASECMS_BASE_URL",
    "BASECMS_TOKEN",
    "BASECMS_APP_ID",
    "BASECMS_NONCE",
    "BASECMS_SECRET",
    "BASECMS_TOKEN_URL",
    "AGENT_LEGION_CMS_TOKEN",
    "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
    "AGENT_LEGION_REMOTE_WORKER_TOKEN",
)


def pytest_configure() -> None:
    if os.environ.get("AGENT_LEGION_TEST_REAL_CMS") != "1":
        os.environ.setdefault("AGENT_LEGION_SKIP_DOTENV", "1")
        for key in _CMS_ENV_KEYS:
            os.environ[key] = ""


# Smoke tier (GATE_TIER=smoke, used by pre-push): a small set of fast,
# high-value tests that keeps the local push feedback loop around a minute
# while the full quick suite stays the CI boundary. Membership is path-based:
# every architecture governance test is smoke by default, plus one core
# behavioral file per subsystem. Add new entries here when a new subsystem
# gains tests; keep the tier under ~90s.
_SMOKE_TEST_FILES = frozenset(
    {
        "tests/routes/test_auth_routes.py",
        "tests/routes/jobs/test_job_lifecycle.py",
        "tests/services/test_vault.py",
        "tests/executors/test_shard_contract.py",
        "tests/executors/test_executor_kinds.py",
        "tests/executors/leases/test_claim_basics.py",
        "tests/workflows/test_sharding.py",
        "tests/db/test_retry.py",
    }
)


def pytest_collection_modifyitems(config, items):
    root = config.rootpath
    for item in items:
        try:
            rel = item.path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in _SMOKE_TEST_FILES or item.path.name.startswith("test_architecture_"):
            item.add_marker(pytest.mark.smoke)


def _rebuild_schema() -> None:
    """Drop and recreate the per-xdist-worker schema, then apply full DDL."""
    close_database_pools()
    try:
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(TEST_SCHEMA))
            )
            conn.execute(sql.SQL("create schema {}").format(sql.Identifier(TEST_SCHEMA)))
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    init_db(TEST_DATABASE_URL)


def _reset_schema_data() -> None:
    """Empty every table without touching DDL (~50ms vs ~2.3s for a rebuild).

    schema_migrations keeps its row: it is constant after init_db, and tests
    that re-run init_db rely on it for idempotency. DDL-seeded rows must be
    restored after the truncate: job_event_seq carries a singleton counter
    row (postgres_schema.sql) that job intake bumps on every batch.
    """
    try:
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "select tablename from pg_tables where schemaname = %s", (TEST_SCHEMA,)
                ).fetchall()
                if row[0] != "schema_migrations"
            ]
            if tables:
                conn.execute(
                    sql.SQL("truncate {} restart identity cascade").format(
                        sql.SQL(", ").join(sql.Identifier(TEST_SCHEMA, t) for t in tables)
                    )
                )
            conn.execute(
                sql.SQL(
                    "insert into {}(id, value) values (1, 0) on conflict(id) do nothing"
                ).format(sql.Identifier(TEST_SCHEMA, "job_event_seq"))
            )
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )


@pytest.fixture(scope="session", autouse=True)
def _session_test_schema():
    """Build the per-worker schema once per session and cache agent definitions.

    Per-test isolation is TRUNCATE-based (see _isolate_postgres_database); a
    full rebuild per test cost ~2.3s and buried the shared Postgres under DDL
    churn. Tests that mutate DDL must opt into a real rebuild via
    @pytest.mark.fresh_schema.
    """
    _rebuild_schema()
    configured = load_application_config(PROJECT_ROOT).config
    yield load_agent_definitions(configured.get("agents", {}))
    close_database_pools()


@pytest.fixture(autouse=True)
def _isolate_postgres_database(request, _session_test_schema):
    if request.node.get_closest_marker("no_db") is not None:
        # Tests marked no_db never touch the database (pure static governance
        # checks, fully mocked script tests); skip TRUNCATE-based isolation.
        yield
        return
    fresh = request.node.get_closest_marker("fresh_schema") is not None
    if fresh:
        _rebuild_schema()
    else:
        close_database_pools()
        _reset_schema_data()
    sync_agent_definitions(TEST_DATABASE_URL, _session_test_schema)
    yield
    if fresh:
        # Erase any DDL drift the test left behind so later TRUNCATE-isolated
        # tests on this worker see the pristine schema.
        _rebuild_schema()
    else:
        close_database_pools()


@pytest.fixture(autouse=True)
def _isolate_project_dotenv(monkeypatch):
    """Keep unit tests from inheriting real local credentials by default.

    Production and local app runs still load the project .env normally. Tests
    that intentionally exercise real CMS credentials can opt in with
    AGENT_LEGION_TEST_REAL_CMS=1.
    """
    if os.environ.get("AGENT_LEGION_TEST_REAL_CMS") == "1":
        return
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    for key in _CMS_ENV_KEYS:
        monkeypatch.setenv(key, "")


@pytest.fixture(autouse=True)
def _block_real_cms_http(monkeypatch):
    if os.environ.get("AGENT_LEGION_TEST_REAL_CMS") == "1":
        return
    original_request = requests.sessions.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        host = urlparse(str(url)).hostname or ""
        if host == "cms.example.com":
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


@pytest.fixture(autouse=True)
def _fast_password_hashing(monkeypatch):
    """Tests mint a session per client; keep pbkdf2 cheap so the suite stays fast."""
    monkeypatch.setattr("server.app.auth.passwords._ITERATIONS", 1_000)


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)


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
    def factory(authenticated: bool = True, **app_options):
        app = app_factory(**app_options)
        with TestClient(app) as client:
            if authenticated:
                # Every test schema starts empty; bootstrap the first admin and
                # keep its session cookie so existing tests stay authenticated.
                response = client.post(
                    "/api/auth/bootstrap",
                    json={"username": "admin", "password": "admin-pw"},
                )
                assert response.status_code == 200, response.text
                client.headers["x-agent-legion-request"] = "1"
            yield client

    return factory


@pytest.fixture
def client(client_factory):
    with client_factory() as c:
        yield c


@pytest.fixture
def anon_client(client_factory):
    """Unauthenticated client for auth-matrix tests (no session cookie)."""
    with client_factory(authenticated=False) as c:
        yield c
