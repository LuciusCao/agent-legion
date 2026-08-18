import json
import os
from contextlib import contextmanager
from pathlib import Path
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

from server.app.db.connection import close_database_pools
from server.app.db.schema import init_db
from server.app.events.agents import AgentStatusManager
from server.app.jobs import JobQueries
from server.app.services.agent_service import reset_published_agent_cache
from server.app.services.skill_source_store import SkillSourceStore
from server.app.services.workflow_catalog import seed_builtin_workflow_catalog
from server.app.settings import load_settings
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES

# Test Agent catalog: Agent definitions are workspace-scoped (schema v46), so
# there is no global seed here — workspaces do not exist at schema-reset time.
# Tests seed the built-in demo agents into their own workspace via
# tests/helpers.seed_workspace_agent_definitions (API-created workspaces
# binding the demo workflow get the demo seed automatically through
# ensure_active_revision).


# Test executor catalog: none. Executor definitions are retired (schema v47,
# P-0.5): the v47 migration harvests their declarations onto workflow
# revision nodes, and the runtime registry is the single implicit code pool.


# Test demo node codes: the demo workflow's two code nodes are global
# (workspace-NULL) factory seeds since #96, mirroring the app startup seed
# (seed_demo_node_codes) so dispatch and Studio reads see them after every
# TRUNCATE.
def _seed_demo_node_codes() -> None:
    from server.app.services.node_codes import NodeCodeService

    repo_root = Path(__file__).resolve().parents[1]
    codes = NodeCodeService(TEST_DATABASE_URL)
    for node_key, relative in (
        ("intake_knowledge_points", "workflow_nodes/example_intake.py"),
        ("publish_content", "workflow_nodes/example_publish.py"),
    ):
        codes.seed_global(
            "education_video_problems_generation",
            node_key,
            (repo_root / relative).read_text(encoding="utf-8"),
            "test seed",
        )


# Test skill sources: the built-in constants (retired config/skills.yaml +
# skills.lock transcription) re-seeded into global_settings after every
# TRUNCATE, mirroring the app startup seed so DB-driven skill resolution
# (SkillManager, skill catalog, openclaw skill_safety) sees the pinned skills.
def _seed_skill_sources() -> None:
    store = SkillSourceStore(TEST_DATABASE_URL)
    store.put_sources(BUILTIN_SKILL_SOURCES.model_copy(deep=True))
    store.put_lock(BUILTIN_SKILL_LOCK.model_copy(deep=True))


# Test workflow catalog: the built-in workflow registry (workflow_catalog
# table, schema v40) re-seeded after every TRUNCATE, mirroring the app startup
# seed so catalog reads (routes, workspace binding, worker scan) see the
# built-in demo workflow.
def _seed_workflow_catalog() -> None:
    seed_builtin_workflow_catalog(TEST_DATABASE_URL)


# Deterministic pricing seeded into global_settings after every TRUNCATE (see
# _reset_schema_data); rates mirror the retired yaml defaults so historical
# cost assertions stay valid.
_TEST_PRICING_DOCUMENT = {
    "currency": "CNY",
    "pricing": [
        {
            "provider": "gateway",
            "model": "your-model-a",
            "input_per_1m": 3.0,
            "output_per_1m": 15.0,
            "cache_read_per_1m": 0.6,
        },
        {
            "provider": "doubao",
            "model": "Doubao-Seed-2.1-turbo",
            "input_per_1m": 3.0,
            "output_per_1m": 15.0,
            "cache_read_per_1m": 0.6,
        },
        {
            "provider": "gateway",
            "model": "your-model-b",
            "input_per_1m": 1.0,
            "output_per_1m": 2.0,
            "cache_read_per_1m": 0.2,
        },
        {
            "provider": "deepseek",
            "model": "your-model-b",
            "input_per_1m": 1.0,
            "output_per_1m": 2.0,
            "cache_read_per_1m": 0.2,
        },
    ],
}

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


# Files that connect to PostgreSQL directly instead of through a root fixture.
# Keep this inventory explicit so new direct consumers are visible in review;
# fixture-based consumers are classified by _POSTGRES_FIXTURES below.
_POSTGRES_TEST_FILES = frozenset(
    {
        "tests/ci/test_executor_worker_stress.py",
        "tests/db/test_agent_catalog_cutover_migration.py",
        "tests/db/test_agent_workspace_scope_migration.py",
        "tests/db/test_agent_request_kind_schema.py",
        "tests/db/test_auth_scoped_tokens_migration.py",
        "tests/db/test_studio_chat_schema.py",
        "tests/db/test_custom_node_codes_migration.py",
        "tests/db/test_executor_retirement_migration.py",
        "tests/db/test_external_connections_migration.py",
        "tests/db/test_hmac_connection_type_migration.py",
        "tests/db/test_job_status_counts_migration.py",
        "tests/db/test_monitoring_hotpath_indexes.py",
        "tests/db/test_node_cms_config_migration.py",
        "tests/db/test_postgres_runtime.py",
        "tests/db/test_quality_loop_schema.py",
        "tests/db/test_versioned_entities_migration.py",
        "tests/db/test_workflow_catalog_migration.py",
        "tests/db/test_workspace_cms_migration.py",
        "tests/db/test_workspace_secrets_migration.py",
        "tests/executors/leases/test_expire_race.py",
        "tests/executors/leases/test_shard_expiry.py",
        "tests/executors/test_leases.py",
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
        "tests/routes/jobs/test_workflow_catalog.py",
        "tests/routes/jobs/test_workflow_upgrade.py",
        "tests/routes/test_agent_workers.py",
        "tests/routes/test_agent_worker_result_spool.py",
        "tests/routes/test_video_job_projection.py",
        "tests/routes/test_workspace_secrets.py",
        "tests/test_cors.py",
        "tests/test_workflow_draft_compare.py",
        "tests/routes/test_workflow_draft_publish_routes.py",
        "tests/routes/test_workflow_draft_validate.py",
        "tests/test_workspace_executor_configuration_flow.py",
        "tests/test_workspace_job_control_flow.py",
        "tests/test_workspace_settings_api.py",
        "tests/routes/jobs/test_workspace_configuration.py",
        "tests/routes/jobs/test_workspace_crud.py",
        "tests/routes/test_artifacts_route.py",
        "tests/routes/test_metrics.py",
        "tests/routes/test_quality.py",
        "tests/routes/test_quality_replay_routes.py",
        "tests/routes/test_skill_sources.py",
        "tests/routes/test_workspace_agent_routes.py",
        "tests/scripts/test_backfill_comprehension_ids.py",
        "tests/scripts/test_backfill_comprehension_jobdir_ids.py",
        "tests/services/test_agent_artifacts.py",
        "tests/services/test_agent_broker_claim_scan.py",
        "tests/services/test_agent_version_pin.py",
        "tests/services/test_agent_worker_liveness.py",
        "tests/services/test_artifact_orphan_gc.py",
        "tests/services/test_builtin_agent_seed.py",
        "tests/services/test_code_claim.py",
        "tests/services/test_code_claim_sweeper.py",
        "tests/services/test_code_dispatch.py",
        "tests/services/test_artifact_store.py",
        "tests/services/test_job_rerun_batch.py",
        "tests/services/test_job_rerun_preview.py",
        "tests/services/test_ops_metrics.py",
        "tests/services/test_studio_chat_service.py",
        "tests/services/test_studio_chat_availability.py",
        "tests/services/test_quality_labels.py",
        "tests/services/test_quality_replays.py",
        "tests/services/test_quality_sampling.py",
        "tests/services/test_quality_stats.py",
        "tests/services/test_scoped_tokens.py",
        "tests/services/test_skill_source_store.py",
        "tests/services/test_workflow_catalog_store.py",
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
        "tests/services/test_agent_broker_reaper.py",
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
        "tests/test_relative_path_portability.py",
        "tests/test_run_dir_cleanup.py",
        "tests/test_skill_catalog_service.py",
        "tests/test_worker_control_db.py",
        "tests/test_workflow_catalog_service.py",
        "tests/test_workflow_execution_control.py",
        "tests/test_workflow_revisions.py",
        "tests/test_workflow_worker_concurrency.py",
        "tests/test_workspace_executor_queries.py",
        "tests/workers/test_scheduler_wakeup.py",
        "tests/workers/test_scan_hot_reload.py",
        "tests/workers/test_workflow_catalog_scan.py",
        "tests/workers/test_workflow_worker_capacity.py",
        "tests/workers/test_workflow_worker_mark_scan.py",
        "tests/workers/test_workflow_worker_node_code.py",
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
    global _SEED_SNAPSHOT
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
    _SEED_SNAPSHOT = None


# Tables re-seeded after every reset (see _isolate_postgres_database). After
# the first full service-layer seed of a session their rows are snapshotted;
# later resets replay the snapshot with plain multi-row INSERTs instead of
# re-running the service-layer seed (~70ms/test) per test. The snapshot is
# invalidated by every schema rebuild, so DDL drift can never stale it.
#
# Note: the replayed rows are byte-frozen at capture time — timestamps inside
# seed rows do NOT advance between tests. A test asserting a seeded row is
# "fresh" (e.g. updated_at >= now - interval) would false-red; assert
# presence/content, never recency, against seeded rows.
_SEEDED_TABLES = ("job_event_seq", "global_settings", "versioned_entities", "workflow_catalog")
_SEED_SNAPSHOT: dict[str, tuple[list[str], list[tuple]]] | None = None


def _capture_seed_snapshot() -> None:
    global _SEED_SNAPSHOT
    snapshot: dict[str, tuple[list[str], list[tuple]]] = {}
    with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
        for table in _SEEDED_TABLES:
            cursor = conn.execute(
                sql.SQL("select * from {}").format(sql.Identifier(TEST_SCHEMA, table))
            )
            columns = [col.name for col in cursor.description]
            snapshot[table] = (columns, cursor.fetchall())
    _SEED_SNAPSHOT = snapshot


def _restore_seed_rows(conn, tables: list[str]) -> None:
    for table in tables:
        columns, rows = _SEED_SNAPSHOT[table]
        if not rows:
            continue
        row_sql = (
            sql.SQL("(") + sql.SQL(", ").join(sql.Placeholder() for _ in columns) + sql.SQL(")")
        )
        conn.execute(
            sql.SQL("insert into {} ({}) values {}").format(
                sql.Identifier(TEST_SCHEMA, table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(row_sql for _ in rows),
            ),
            [value for row in rows for value in row],
        )


def _dirty_tables(conn, tables: list[str]) -> set[str]:
    """Tables holding rows or owning an advanced identity sequence.

    Row existence is an exact per-table EXISTS probe (one round trip), not a
    stats-estimator read, so a freshly written table can never be misjudged as
    clean. Sequence state matters because a table can be empty while its
    identity sequence advanced (rows inserted, then deleted); only TRUNCATE
    ... RESTART IDENTITY rewinds that, so such tables stay in the truncate
    set. Any detection error falls back to "everything dirty" — missing a
    dirty table would leak data between tests, which is worse than slow.

    Precondition: every sequence in the test schema is column-owned (serial /
    identity / owned default), so the pg_depend auto/internal join below
    reaches it. A standalone CREATE SEQUENCE (no owning column) is invisible
    here; the current schema has none — if one is ever added, it must be
    rewound explicitly in _reset_schema_data.
    """
    try:
        probes = sql.SQL(", ").join(
            sql.SQL("exists(select 1 from {}) as {}").format(
                sql.Identifier(TEST_SCHEMA, table), sql.Identifier(table)
            )
            for table in tables
        )
        row = conn.execute(sql.SQL("select {}").format(probes)).fetchone()
        dirty = {table for table, has_rows in zip(tables, row, strict=True) if has_rows}
        sequence_rows = conn.execute(
            """
            select t.relname
            from pg_class s
            join pg_namespace n on n.oid = s.relnamespace
            join pg_depend d on d.objid = s.oid and d.deptype in ('a', 'i')
            join pg_class t on t.oid = d.refobjid
            join pg_sequences ps
              on ps.schemaname = n.nspname and ps.sequencename = s.relname
            where s.relkind = 'S' and n.nspname = %s and ps.last_value is not null
            """,
            (TEST_SCHEMA,),
        ).fetchall()
        dirty.update(seq_row[0] for seq_row in sequence_rows)
        return dirty
    except psycopg.Error:
        return set(tables)


def _reset_schema_data() -> bool:
    """Empty dirty tables without touching DDL, then restore seeded rows.

    Returns True when the reset replayed the seed snapshot (seeded tables
    restored inline); False when a full service-layer seed must run (first
    reset after a schema build, snapshot not captured yet).

    Only tables that actually hold rows (or own an advanced identity
    sequence) are truncated; clean tables are left alone. Seeded tables are
    effectively always dirty, so their per-test restoration is certain; the
    seeded content itself is bit-identical to the service-layer seed because
    the snapshot was captured from it. schema_migrations keeps its rows: it
    is constant after init_db, and tests that re-run init_db rely on it for
    idempotency.
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
            if _SEED_SNAPSHOT is None:
                dirty = set(tables)
            else:
                dirty = _dirty_tables(conn, tables)
                # Seeded tables are always re-truncated and replayed: a test
                # that deleted seed rows without adding new ones would
                # otherwise look "clean" to the row probe and lose its seeds.
                dirty.update(t for t in _SEEDED_TABLES if t in tables)
            if dirty:
                conn.execute(
                    sql.SQL("truncate {} restart identity cascade").format(
                        sql.SQL(", ").join(sql.Identifier(TEST_SCHEMA, t) for t in dirty)
                    )
                )
            if _SEED_SNAPSHOT is not None:
                _restore_seed_rows(conn, [t for t in _SEEDED_TABLES if t in dirty])
                return True
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    # First reset after a (re)build: keep the historical full-seed path. The
    # job_event_seq singleton counter row (postgres_schema.sql) is bumped by
    # job intake on every batch, and global_settings gets a fixed token_usage
    # pricing document so cost-calculation tests have deterministic rates.
    try:
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "insert into {}(id, value) values (1, 0) on conflict(id) do nothing"
                ).format(sql.Identifier(TEST_SCHEMA, "job_event_seq"))
            )
            conn.execute(
                sql.SQL(
                    "insert into {}(key, value) values ('token_usage', %s)"
                    " on conflict(key) do update set value=excluded.value"
                ).format(sql.Identifier(TEST_SCHEMA, "global_settings")),
                (json.dumps(_TEST_PRICING_DOCUMENT),),
            )
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    return False


@pytest.fixture(scope="session")
def _session_test_schema():
    """Build the per-worker schema once per session.

    Per-test isolation is TRUNCATE-based (see _isolate_postgres_database); a
    full rebuild per test cost ~2.3s and buried the shared Postgres under DDL
    churn. Tests that mutate DDL must opt into a real rebuild via
    @pytest.mark.fresh_schema.
    """
    ensure_test_database()
    _rebuild_schema()
    yield
    close_database_pools()


@pytest.fixture(autouse=True)
def _isolate_postgres_database(request):
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
    if fresh:
        _rebuild_schema()
        reset_published_agent_cache()
        _seed_demo_node_codes()
        _seed_skill_sources()
        _seed_workflow_catalog()
        _capture_seed_snapshot()
    else:
        close_database_pools()
        replayed = _reset_schema_data()
        reset_published_agent_cache()
        if not replayed:
            _seed_demo_node_codes()
            _seed_skill_sources()
            _seed_workflow_catalog()
            _capture_seed_snapshot()
    yield
    if fresh:
        # Erase any DDL drift the test left behind so later TRUNCATE-isolated
        # tests on this worker see the pristine schema.
        _rebuild_schema()
    else:
        close_database_pools()


@pytest.fixture(autouse=True)
def _assert_shared_app_invariants():
    """Fail a test that left the worker-session shared app dirty.

    Naming is load-bearing: same-scope autouse fixtures are set up in
    alphabetical order (and torn down in reverse), and monkeypatch is torn
    down only after every fixture that grabbed it. Sorting before
    ``_block_real_cms_http`` — the first autouse fixture that requests
    monkeypatch — puts this teardown after the monkeypatch undo, so it
    observes the post-undo in-memory state of any shared app the test
    touched. fresh=True apps are private and never tracked.
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
    # The repo yaml no longer carries a global cms: section; tests loading the
    # real settings get the fake CMS host below through the supported env
    # channel (node/workspace config still overrides it, as in production).
    monkeypatch.setenv("CMS_BASE_URL", "https://cms.example.com/v2")
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
