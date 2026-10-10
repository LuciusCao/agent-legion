import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.isolation_support import (
    capture_seed_snapshot,
    close_reset_connection,
    invalidate_reset_state,
    reset_schema_data,
)
from tests.postgres_support import (
    BASE_DATABASE_URL,
    TEST_DATABASE_URL,
    TEST_SCHEMA,
    close_database_pools_settled,
    ensure_test_database,
    settle_database_pools,
)

os.environ["AGENT_LEGION_DATABASE_URL"] = TEST_DATABASE_URL

import psycopg
from psycopg import sql

from server.app.db.schema import init_db
from server.app.db.schema_head_cache import init_db_full_check, note_schema_rebuilt
from server.app.events.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.agent_service import reset_published_agent_cache
from server.app.settings import load_settings

# Test Agent catalog: Agent definitions are workspace-scoped (schema v46), so
# there is no global seed here — workspaces do not exist at schema-reset time.
# Tests seed the built-in demo agents into their own workspace via
# tests/helpers.seed_workspace_agent_definitions, and revisions via
# tests/helpers.publish_builtin_revision / publish_legacy_intake_revision
# (schema v62: workspace creation no longer seeds the demo template —
# ensure_active_revision runs only through `make import-demo` /
# scripts/seed_demo.py).


# Test executor catalog: none. Executor definitions are retired (schema v47,
# P-0.5): the v47 migration harvests their declarations onto workflow
# revision nodes, and the runtime registry is the single implicit code pool.


# Test skill lock: none. The skill_sources registry is retired (#322) and the
# lock starts empty — pinned refs auto-lock on first dispatch; tests that
# need a frozen pin seed global_settings themselves.


# Deterministic pricing seeded into global_settings after every TRUNCATE:
# tests/isolation_support.py owns the document and the replay (see
# reset_schema_data); rates mirror the retired yaml defaults so historical
# cost assertions stay valid.

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
    "AGENT_LEGION_REMOTE_WORKER_TOKEN",
)

# #641: the budget-monotonicity anchor env vars reshape the architecture
# guards globally when they leak from a developer shell (e.g. exporting the
# release-train flag to simulate a train merge, then running pytest — the
# HEAD^-dependent monotonicity self-tests silently lose their second anchor).
# Tests that need them set their own via monkeypatch; the session starts clean.
_BUDGET_ANCHOR_ENV_KEYS = (
    "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN",
    "AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW",
    "AGENT_LEGION_BUDGET_BASE",
)


def pytest_configure() -> None:
    os.environ.setdefault("AGENT_LEGION_SKIP_DOTENV", "1")
    for key in _CMS_ENV_KEYS:
        os.environ[key] = ""
    for key in _BUDGET_ANCHOR_ENV_KEYS:
        os.environ.pop(key, None)


# Smoke tier (GATE_TIER=smoke, used by pre-push): a small set of fast,
# high-value tests that keeps the local push feedback loop around a minute
# while the full quick suite stays the CI boundary. Membership is path-based:
# every architecture governance test is smoke by default, plus one core
# behavioral file per subsystem. The manifest lives in
# config/architecture/smoke-test-files.json (#192); add new entries there
# when a new subsystem gains tests; keep the tier under ~90s.


# Files that connect to PostgreSQL directly instead of through a root fixture.
# Keep this inventory explicit so new direct consumers are visible in review;
# fixture-based consumers are classified by _POSTGRES_FIXTURES below. Both
# directions are enforced by tests/app/test_pytest_postgres_boundaries.py: a file
# importing tests.postgres_support (or calling psycopg.connect) must be listed
# in the manifest, and every listed path must still exist. The manifest lives
# in config/architecture/postgres-test-files.json (#192).
def _load_manifest(name: str) -> frozenset[str]:
    path = Path(__file__).resolve().parents[1] / "config" / "architecture" / name
    document = json.loads(path.read_text(encoding="utf-8"))
    files = document.get("files")
    if not isinstance(files, list) or not all(isinstance(entry, str) for entry in files):
        raise RuntimeError(f"malformed manifest {path}: files must be a list of strings")
    return frozenset(files)


_SMOKE_TEST_FILES = _load_manifest("smoke-test-files.json")
_POSTGRES_TEST_FILES = _load_manifest("postgres-test-files.json")

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
        if rel in _SMOKE_TEST_FILES or item.path.name.startswith("test_architecture_"):
            item.add_marker(pytest.mark.smoke)
        if (
            rel in _POSTGRES_TEST_FILES
            or _POSTGRES_FIXTURES.intersection(item.fixturenames)
            or item.get_closest_marker("fresh_schema") is not None
        ):
            item.add_marker(pytest.mark.postgres)


def _rebuild_schema() -> None:
    """Drop and recreate the per-xdist-worker schema, then apply full DDL."""
    close_database_pools_settled()
    close_reset_connection()
    # Invalidate BEFORE the drop: a create-schema failure below must not
    # leave the memo claiming "at head" over an empty schema.
    note_schema_rebuilt()
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
    invalidate_reset_state()


@pytest.fixture(scope="session")
def _session_test_schema():
    """Build the per-worker schema once per session.

    Per-test isolation is TRUNCATE-based (see _isolate_postgres_database); a
    full rebuild per test cost ~2.3s and buried the shared Postgres under DDL
    churn. Tests that mutate DDL must opt into a real rebuild via
    @pytest.mark.fresh_schema; their post-test rebuild is deferred to the
    next test's setup via the _SCHEMA_DIRTY flag, so consecutive
    fresh_schema tests share one rebuild and the session never pays for a
    rebuild nobody runs against. Pools stay open for the whole session and
    are only closed here (and around schema rebuilds); the isolation
    maintenance connection (isolation_support.reset_connection) follows the
    same lifecycle.
    """
    ensure_test_database()
    _rebuild_schema()
    yield
    close_database_pools_settled()
    close_reset_connection()


@pytest.fixture(autouse=True)
def _reset_result_unpack_pool(_assert_shared_app_invariants):
    """#552：result 解包进程池是模块级单例——用过它的测试收尾时必须回收，
    否则泄漏的 SpawnProcess 会被「无残留子进程」类断言（如
    tests/full/test_executor_cancellation_recovery.py）抓到。池未创建时
    reset 是纯 no-op（无进程可杀），不产生每测试开销。#569 的
    result_validate_pool 同款单例一并回收。"""
    yield
    from server.app.agent_broker import result_unpack_pool, result_validate_pool

    result_unpack_pool.reset_pool()
    # #554：configure() 钉入的实例设置值同为模块级状态，一并复位，
    # 防测试间串味（monkeypatch 之外的直改场景）。
    result_unpack_pool.configure(0)
    result_validate_pool.reset_pool()
    result_validate_pool.configure(0)


# Set by a fresh_schema test's teardown instead of rebuilding the schema
# there; the next postgres test on this worker (fresh or plain) rebuilds
# once at the start of its isolation setup and clears the flag. Deferring
# the rebuild halves fresh_schema isolation cost (consecutive fresh tests
# share one rebuild, and a dirty flag left at session end triggers no
# rebuild nobody will use). Module-level is correct: this conftest is
# imported once per xdist worker process.
_SCHEMA_DIRTY = False

# Tracks which session-scoped shared clients have been instantiated (the
# fixture names of _shared_authed_client / _shared_anon_client). Guards one
# dirty-window interleave: session fixtures instantiate BEFORE the
# function-scoped autouse isolation setup of their first requesting test,
# so if a fresh_schema test leaves drift and the NEXT test is the first to
# request EITHER shared client, create_app (init_db, plus the lifespan's
# reap_zombie_sessions write) would run against the drifted schema before
# the deferred rebuild fires. Tracking each client separately matters: with
# only one of them created, the other can still instantiate for the first
# time later in the session, so the teardown defers the rebuild only once
# BOTH exist; until then it rebuilds eagerly (early-fresh is rare, so the
# eager cost almost never lands).
_SHARED_SESSION_CLIENTS_CREATED: set[str] = set()
_ALL_SHARED_SESSION_CLIENTS = frozenset({"_shared_authed_client", "_shared_anon_client"})


@pytest.fixture(autouse=True)
def _isolate_postgres_database(_assert_shared_app_invariants, request):
    if request.node.get_closest_marker("no_db") is not None:
        # Tests marked no_db never touch the database (pure static governance
        # checks, fully mocked script tests); skip TRUNCATE-based isolation.
        yield
        return
    if request.node.get_closest_marker("postgres") is None:
        yield
        return

    request.getfixturevalue("_session_test_schema")
    fresh = request.node.get_closest_marker("fresh_schema") is not None
    global _SCHEMA_DIRTY
    if _SCHEMA_DIRTY or fresh:
        # Restore the pristine baseline schema: either a previous
        # fresh_schema test left DDL drift behind (dirty flag), or this
        # test opted into a guaranteed-fresh schema. The flag must fire for
        # the next test of ANY kind — plain tests assume the baseline
        # schema for their TRUNCATE isolation. A single rebuild covers both
        # conditions. _rebuild_schema closes the pools first (settled): a
        # pooled connection's search_path points into the schema being
        # dropped, and fresh_schema tests are rare enough that the
        # close/rebuild cost is acceptable there.
        _rebuild_schema()
        _SCHEMA_DIRTY = False
    if fresh:
        reset_published_agent_cache()
        capture_seed_snapshot()
    else:
        # Pools stay alive across tests: the settle barrier is the only
        # per-test synchronization TRUNCATE needs (queued dirty-return
        # rollbacks must have run before it takes AccessExclusive — #1045).
        settle_database_pools()
        replayed = reset_schema_data()
        reset_published_agent_cache()
        if not replayed:
            capture_seed_snapshot()
    yield
    if fresh:
        # Defer the post-test rebuild to the next test's setup (see
        # _SCHEMA_DIRTY): consecutive fresh_schema tests share one rebuild,
        # and the session never pays for a rebuild nobody runs against.
        # Exception: until BOTH session-scoped shared clients have been
        # instantiated, rebuild NOW — a not-yet-created client's first
        # instantiation would otherwise run against the drifted schema
        # before the deferred rebuild fires (see
        # _SHARED_SESSION_CLIENTS_CREATED).
        if _SHARED_SESSION_CLIENTS_CREATED >= _ALL_SHARED_SESSION_CLIENTS:
            _SCHEMA_DIRTY = True
        else:
            _rebuild_schema()


@pytest.fixture(autouse=True)
def _init_db_full_check_during_fresh_schema(_assert_shared_app_invariants, request):
    """Keep init_db's process-local head memo out of DDL-mutating tests.

    fresh_schema tests rewind ``schema_migrations`` mid-test and expect the
    next ``init_db`` to re-run the upgrade; the memo (schema_head_cache)
    must not short-circuit those calls. Non-fresh tests keep the memo: their
    repeat ``init_db`` calls are the steady-state no-op it exists to skip.
    """
    if request.node.get_closest_marker("fresh_schema") is None:
        yield
        return
    with init_db_full_check():
        yield


@pytest.fixture(autouse=True)
def _assert_shared_app_invariants():
    """Fail a test that left the worker-session shared app dirty.

    The guard must run its teardown AFTER the monkeypatch undo: a test may
    scope an app.state mutation with monkeypatch (auto-restored), and the
    guard has to observe the post-undo state or it would false-red on
    exactly those tests. Teardown is reverse setup order, so the guard is
    the first autouse fixture set up: every other autouse fixture in this
    conftest declares ``_assert_shared_app_invariants`` as its first
    parameter. That makes the ordering an explicit dependency chain instead
    of alphabetical fixture-name sorting; the structure is enforced by
    tests/app/test_pytest_postgres_boundaries.py. fresh=True apps are private
    and never tracked.
    """
    _SHARED_APP_USAGE.clear()
    yield
    apps = list(_SHARED_APP_USAGE)
    _SHARED_APP_USAGE.clear()
    errors = []
    for app in apps:
        errors.extend(_check_shared_app_invariants(app))
    if errors:
        raise AssertionError("shared app invariant violated: " + "; ".join(errors))


@pytest.fixture(autouse=True)
def _isolate_project_dotenv(_assert_shared_app_invariants, monkeypatch):
    """Keep unit tests from inheriting real local credentials by default.

    Production and local app runs still load the project .env normally.
    """
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    for key in _CMS_ENV_KEYS:
        monkeypatch.setenv(key, "")


@pytest.fixture(autouse=True)
def _fast_password_hashing(_assert_shared_app_invariants, monkeypatch):
    """Tests mint a session per client; keep pbkdf2 cheap so the suite stays fast.

    Same harness relaxation for the #970 new-password length floor: fixture
    accounts across the suite use short throwaway passwords. The weak-list
    rule stays live; tests/routes/test_password_policy.py re-tightens the
    length to pin the production baseline.
    """
    monkeypatch.setattr("server.app.auth.passwords._ITERATIONS", 1_000)
    monkeypatch.setattr("server.app.auth.password_policy.MIN_PASSWORD_LENGTH", 1)


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

    def factory(*, configure=None):
        app = create_app(data_dir=tmp_path, start_worker=False)
        if configure is not None:
            configure(app)
        return app

    return factory


# Shared session-scoped apps: create_app costs 0.5-1.2s (FastAPI route
# registration + pydantic schema generation), so the default client fixtures
# reuse one long-lived app per xdist worker instead of rebuilding it per test.
# The lifespan runs once per worker session (per-test lifespan would trip
# shutdown hooks that are not re-armable, e.g. agent_dispatch.enqueue_pool
# and StudioChatService._shutdown).
#
# Isolation contract: anything DB-backed (users, workspaces, broker claims,
# worker control state) is still reset per test by the TRUNCATE in
# _isolate_postgres_database; cookies/headers are reset per test below.
# In-memory app.state, however, now survives across tests. Tests that mutate
# it must either scope the mutation with monkeypatch (auto-restored) or opt
# out to a private app via client_factory(fresh=True) / app options. Known
# in-memory mutable points: settings (incl. executor_runtime flags),
# agent_manager.agents, executor_registry (publish/rollback/archive hot
# reload), app.state.workflow_worker. And a test must never re-enter the
# shared client's context manager (`with client as c`): that would run the
# app lifespan a second time, and its exit would fire the shutdown hooks
# (cancel background tasks, close the enqueue pool, shut down studio chat)
# on the still-shared app.
#
# The shared app's data_dir is session-scoped and NOT reset between tests:
# job artifact paths are job-id-derived, so a re-issued job id (the DB-side
# sequence rewinds per test) collides with the previous test's leftover
# files. Tests that assert on the filesystem must use
# client_factory(fresh=True) — that is also why the job_db fixture's tmp_path
# jobs_dir deliberately diverges from the shared app's jobs_dir.
def _build_shared_client(tmp_path_factory, dir_name: str):
    from server.app.main import create_app

    data_dir = tmp_path_factory.mktemp(dir_name)
    app = create_app(data_dir=data_dir, start_worker=False)
    return app


@contextmanager
def _no_background_tasks():
    """Dormant BackgroundTasks.start for the worker-session shared apps.

    The shared apps' background loops would outlive individual tests and act
    on the shared per-worker schema *between* tests: the intake consumer could
    claim batches enqueued by a fresh-app test (processing them against the
    wrong data_dir), and the ops-metrics/aggregator loops could write rows a
    later test does not expect. Function-scoped apps keep the full production
    behavior; only the two session apps run with the loops disabled. Tests
    that need a background loop (e.g. the agent-status broadcast flush) must
    use a private app via client_factory(fresh=True).
    """
    from unittest import mock

    from server.app.startup_tasks import BackgroundTasks

    with mock.patch.object(BackgroundTasks, "start", lambda self, app: None):
        yield


@pytest.fixture(scope="session")
def _shared_authed_client(tmp_path_factory, _session_test_schema):
    # _session_test_schema is an explicit dependency: session fixtures run
    # before the function-scoped autouse isolation fixture, and create_app
    # needs the worker schema to already exist (JobQueries runs init_db).
    app = _build_shared_client(tmp_path_factory, "shared-app")
    # The patch must cover only __enter__ (the lifespan start): keeping it
    # active across the yield would neuter background tasks on every
    # function-scoped app in this worker too.
    client = TestClient(app)
    with _no_background_tasks():
        client.__enter__()
    _SHARED_SESSION_CLIENTS_CREATED.add("_shared_authed_client")
    try:
        yield client, dict(client.headers)
    finally:
        client.__exit__(None, None, None)


@pytest.fixture(scope="session")
def _shared_anon_client(tmp_path_factory, _session_test_schema):
    # A second app (not a second client on the same app): entering two
    # TestClients on one app would run the lifespan twice and re-attach the
    # event bus to the wrong loop.
    app = _build_shared_client(tmp_path_factory, "shared-anon-app")
    client = TestClient(app)
    with _no_background_tasks():
        client.__enter__()
    _SHARED_SESSION_CLIENTS_CREATED.add("_shared_anon_client")
    try:
        yield client, dict(client.headers)
    finally:
        client.__exit__(None, None, None)


def _reset_client_state(client: TestClient, default_headers: dict[str, str]) -> None:
    client.cookies.clear()
    client.headers.clear()
    client.headers.update(default_headers)
    # The login lockout table is in-process (LoginRateLimiter, not DB-backed),
    # so the per-test TRUNCATE cannot reach it; a fresh app starts with an
    # empty table, and the shared app must be restored to the same condition
    # or a lockout test poisons every later login/bootstrap on this worker.
    # Hard attribute references on purpose: a rename inside AuthService or
    # LoginRateLimiter must fail this reset loudly instead of silently
    # skipping it (#91).
    rate_limiter = client.app.state.auth_service._rate_limiter
    rate_limiter._entries.clear()
    # Same in-process class of state for the job-list aggregates: TtlCache
    # (#358) serves snapshot totals/facets with a 7s TTL — longer than a
    # test — and its key does not observe the per-test TRUNCATE. Without
    # this clear, a facets/snapshot read in test N keeps serving test N's
    # counts to test N+1 on the same shared app (observed as total=0-after-
    # truncate poisoning when two #626 token test files run in one session).
    # A fresh app starts with an empty cache, so this restores exactly the
    # fresh-app condition. The service is not on app.state (it is built
    # inside create_job_snapshot_router); reach it through the route
    # closures, and fail soft only if the wiring changes shape — the routes
    # are the thing being served, so a missing closure means the endpoints
    # themselves moved.
    from starlette.routing import Route as _Route

    for route in client.app.routes:
        if not isinstance(route, _Route):
            continue
        if getattr(route.endpoint, "__name__", "") != "snapshot_workspace_jobs":
            continue
        for cell in route.endpoint.__closure__ or ():
            service = cell.cell_contents
            if service.__class__.__name__ == "JobListQueryService":
                service._aggregate_cache.clear()
                break


def _check_shared_app_invariants(app) -> list[str]:
    """Invariants for a worker-session shared app after one test.

    The per-test reset restores DB state only; in-memory app.state survives
    across tests. These O(1) checks turn silent cross-test pollution into a
    red test (tests that must mutate app.state belong on
    client_factory(fresh=True)). The job event buffer is drained rather than
    asserted: the shared apps run with background flush loops disabled, so
    every event-producing test would otherwise accumulate buffered events
    into its successor. The in-memory revision high-water mark still advances
    across tests while the DB-side job_event_seq rewinds per test, so tests
    must never assert absolute revision values against the DB sequence (#91).
    """
    app.state.job_event_buffer.drain_compacted()
    errors = []
    agents = app.state.agent_manager.agents
    if agents:
        errors.append(f"agent_manager.agents not empty after test: {agents!r}")
    return errors


# Apps touched through the shared-client fixtures during the current test;
# consumed by the autouse guard (_assert_shared_app_invariants). Module-level
# (not fixture state) so both the client fixtures and the guard can reach it.
_SHARED_APP_USAGE: list = []


def _track_shared_app(app) -> None:
    if not any(app is used for used in _SHARED_APP_USAGE):
        _SHARED_APP_USAGE.append(app)


def _bootstrap_admin(client: TestClient) -> None:
    # Every test schema starts empty; bootstrap the first admin and keep its
    # session cookie so existing tests stay authenticated.
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "admin", "password": "admin-pw"},
    )
    assert response.status_code == 200, response.text
    client.headers["x-agent-legion-request"] = "1"


@pytest.fixture
def client_factory(app_factory, request):
    @contextmanager
    def factory(authenticated: bool = True, fresh: bool = False, **app_options):
        if not fresh and not app_options:
            # Default path: reuse the worker-session app (see isolation
            # contract above). fresh=True or any app option builds a private
            # function-scoped app instead.
            fixture_name = "_shared_authed_client" if authenticated else "_shared_anon_client"
            client, default_headers = request.getfixturevalue(fixture_name)
            _reset_client_state(client, default_headers)
            if authenticated:
                _bootstrap_admin(client)
            _track_shared_app(client.app)
            yield client
            return
        app = app_factory(**app_options)
        with TestClient(app) as client:
            if authenticated:
                _bootstrap_admin(client)
            yield client

    return factory


@pytest.fixture
def client(_shared_authed_client):
    client, default_headers = _shared_authed_client
    _reset_client_state(client, default_headers)
    _bootstrap_admin(client)
    _track_shared_app(client.app)
    yield client
    _reset_client_state(client, default_headers)


@pytest.fixture
def anon_client(_shared_anon_client):
    """Unauthenticated client for auth-matrix tests (no session cookie)."""
    client, default_headers = _shared_anon_client
    _reset_client_state(client, default_headers)
    _track_shared_app(client.app)
    yield client
    _reset_client_state(client, default_headers)
