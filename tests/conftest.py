import json
import os
from contextlib import contextmanager
from urllib.parse import urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from tests.postgres_support import (
    BASE_DATABASE_URL,
    TEST_DATABASE_URL,
    TEST_SCHEMA,
    ensure_test_database,
)

os.environ["AGENT_LEGION_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AGENT_LEGION_SKIP_MODULE_APP"] = "1"

import psycopg
from psycopg import sql

from server.app.agent_catalog import load_agent_definitions, sync_agent_definitions
from server.app.agents import AgentStatusManager
from server.app.configuration import load_application_config
from server.app.db.connection import close_database_pools
from server.app.db.schema import init_db
from server.app.jobs import JobQueries
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


# Files that connect to PostgreSQL directly instead of through a root fixture.
# Keep this inventory explicit so new direct consumers are visible in review;
# fixture-based consumers are classified by _POSTGRES_FIXTURES below.
_POSTGRES_TEST_FILES = frozenset(
    {
        "tests/ci/test_executor_worker_stress.py",
        "tests/db/test_postgres_runtime.py",
        "tests/db/test_workspace_cms_migration.py",
        "tests/db/test_workspace_secrets_migration.py",
        "tests/executors/leases/test_expire_race.py",
        "tests/executors/leases/test_shard_expiry.py",
        "tests/executors/test_leases.py",
        "tests/executors/test_local_executor.py",
        "tests/full/test_agent_worker_control_plane.py",
        "tests/full/test_executor_cancellation_recovery.py",
        "tests/full/test_executor_worker_fairness.py",
        "tests/full/test_storage_path_corruption.py",
        "tests/full/test_velites_harness_e2e.py",
        "tests/full/test_split_config_startup.py",
        "tests/full/test_workspace_sse.py",
        "tests/routes/jobs/test_failed_node_runs.py",
        "tests/routes/jobs/test_intake_modes.py",
        "tests/routes/jobs/test_job_batches.py",
        "tests/routes/jobs/test_job_lifecycle.py",
        "tests/routes/jobs/test_job_rerun.py",
        "tests/routes/jobs/test_job_run_to.py",
        "tests/routes/jobs/test_openapi_contracts.py",
        "tests/routes/jobs/test_video_jobs_source.py",
        "tests/routes/jobs/test_workflow_catalog.py",
        "tests/routes/jobs/test_workflow_upgrade.py",
        "tests/routes/test_agent_workers.py",
        "tests/routes/test_video_job_projection.py",
        "tests/routes/test_workspace_secrets.py",
        "tests/test_cors.py",
        "tests/test_questions_api.py",
        "tests/test_video_job_intake.py",
        "tests/test_workflow_draft_compare.py",
        "tests/test_workspace_executor_configuration_flow.py",
        "tests/test_workspace_job_control_flow.py",
        "tests/test_workspace_settings_api.py",
        "tests/routes/jobs/test_workspace_configuration.py",
        "tests/routes/jobs/test_workspace_crud.py",
        "tests/routes/test_artifacts_route.py",
        "tests/routes/test_metrics.py",
        "tests/routes/test_workspace_agent_routes.py",
        "tests/services/test_artifact_store.py",
        "tests/services/test_ops_metrics.py",
        "tests/services/test_token_usage.py",
        "tests/test_export_openapi.py",
        "tests/test_jobs_route_contracts.py",
        "tests/test_legacy_worker_disabled.py",
        "tests/test_main.py",
        "tests/routes/test_misc.py",
        "tests/services/test_workflow_draft_publish.py",
        "tests/test_agent_broker.py",
        "tests/test_agent_broker_batch.py",
        "tests/test_agent_broker_concurrency.py",
        "tests/test_agent_broker_empty.py",
        "tests/test_agent_catalog.py",
        "tests/test_agent_stock.py",
        "tests/test_auth_queries.py",
        "tests/test_db.py",
        "tests/test_executor_recovery.py",
        "tests/test_job_event_buffer_db.py",
        "tests/test_job_events.py",
        "tests/test_job_log_service.py",
        "tests/test_job_workflow_upgrade.py",
        "tests/test_jobs.py",
        "tests/test_jobs_queries.py",
        "tests/test_log_cleanup.py",
        "tests/test_pi_runner.py",
        "tests/test_question_comprehension_info_workflow.py",
        "tests/test_relative_path_portability.py",
        "tests/test_run_dir_cleanup.py",
        "tests/test_worker_control_db.py",
        "tests/test_workflow_execution_control.py",
        "tests/test_workflow_revisions.py",
        "tests/test_workflow_worker_concurrency.py",
        "tests/test_workspace_executor_queries.py",
        "tests/workers/test_scheduler_wakeup.py",
        "tests/workers/test_workflow_worker_capacity.py",
        "tests/workers/test_workflow_worker_node_config.py",
        "tests/workers/test_workflow_worker_ready_queue.py",
        "tests/workers/test_workflow_worker_thread_local.py",
        "tests/workers/test_workflow_worker_thread_paths.py",
        "tests/workers/test_workflow_worker_thread_pi.py",
        "tests/workflows/test_pi_runner_token_usage.py",
        "tests/workflows/test_sharding.py",
    }
)

_POSTGRES_FIXTURES = frozenset(
    {
        "anon_client",
        "app_factory",
        "client",
        "client_factory",
        "job_db",
        "queries",
        "repo_a",
        "repo_b",
        "tmp_db",
    }
)


def pytest_collection_modifyitems(config, items):
    root = config.rootpath
    for item in items:
        try:
            rel = item.path.relative_to(root).as_posix()
        except ValueError:
            continue
        if (
            rel in _POSTGRES_TEST_FILES
            or _POSTGRES_FIXTURES.intersection(item.fixturenames)
            or item.get_closest_marker("fresh_schema") is not None
        ):
            item.add_marker(pytest.mark.postgres)


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


@pytest.fixture(scope="session")
def _session_test_schema():
    """Build the per-worker schema once per session and cache agent definitions.

    Per-test isolation is TRUNCATE-based (see _isolate_postgres_database); a
    full rebuild per test cost ~2.3s and buried the shared Postgres under DDL
    churn. Tests that mutate DDL must opt into a real rebuild via
    @pytest.mark.fresh_schema.
    """
    ensure_test_database()
    _rebuild_schema()
    configured = load_application_config(PROJECT_ROOT).config
    yield load_agent_definitions(configured.get("agents", {}))
    close_database_pools()


@pytest.fixture(autouse=True)
def _isolate_postgres_database(request):
    if request.node.get_closest_marker("postgres") is None:
        yield
        return

    agent_definitions = request.getfixturevalue("_session_test_schema")
    fresh = request.node.get_closest_marker("fresh_schema") is not None
    if fresh:
        _rebuild_schema()
    else:
        close_database_pools()
        _reset_schema_data()
    sync_agent_definitions(TEST_DATABASE_URL, agent_definitions)
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
    from server.app.main import create_app

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
