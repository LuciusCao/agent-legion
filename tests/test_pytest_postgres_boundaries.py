from __future__ import annotations

import ast
from pathlib import Path

import psycopg
import pytest

from tests import conftest as test_config
from tests import postgres_support

ROOT = Path(__file__).resolve().parents[1]
DIRECT_POSTGRES_IMPORTS = (
    "from tests.postgres_support",
    "from server.app.main import create_app",
    "from server.app import main",
    "from scripts.export_openapi import build_openapi_schema",
)


class _QueryResult:
    def __init__(self, row: tuple[int] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[int] | None:
        return self._row


class _FakeMaintenanceConnection:
    def __init__(self, *, database_exists: bool) -> None:
        self.database_exists = database_exists
        self.executions: list[tuple[object, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, query, params=None) -> _QueryResult:
        self.executions.append((query, params))
        if isinstance(query, str) and "pg_database" in query:
            return _QueryResult((1,) if self.database_exists else None)
        return _QueryResult()


def test_postgres_support_import_has_no_database_creation_side_effect() -> None:
    source = (ROOT / "tests/postgres_support.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    top_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]

    assert "ensure_test_database" not in top_level_calls


@pytest.mark.parametrize(("database_exists", "expected_execution_count"), [(False, 4), (True, 3)])
def test_database_creation_is_serialized_before_catalog_check(
    monkeypatch: pytest.MonkeyPatch,
    database_exists: bool,
    expected_execution_count: int,
) -> None:
    connection = _FakeMaintenanceConnection(database_exists=database_exists)
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *args, **kwargs: connection,
    )
    monkeypatch.setattr(
        postgres_support,
        "BASE_DATABASE_URL",
        "postgresql://postgres@127.0.0.1/agent_legion_race_test",
    )

    postgres_support.ensure_test_database()

    assert len(connection.executions) == expected_execution_count
    assert "pg_advisory_lock" in connection.executions[0][0]
    assert "pg_database" in connection.executions[1][0]
    # Role-isolation guard: the owner-alignment probe runs last (the fake
    # answers None, so no ALTER follows); the advisory lock still comes first.
    assert "pg_roles" in connection.executions[-1][0]


def test_direct_postgres_consumers_are_in_explicit_inventory() -> None:
    missing: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        if not any(import_text in source for import_text in DIRECT_POSTGRES_IMPORTS):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative not in test_config._POSTGRES_TEST_FILES:
            missing.append(relative)

    assert missing == []


def _ast_calls_psycopg_connect(source: str) -> bool:
    """True when the module really calls psycopg.connect(...).

    Importing psycopg just for its exception classes (retry-path fakes) or
    embedding "psycopg.connect" inside a code string executed by a subprocess
    must NOT count — only a real call node in this module does.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            value = func.value
            if isinstance(value, ast.Name) and value.id == "psycopg":
                return True
            if isinstance(value, ast.Attribute) and value.attr == "psycopg":
                return True
    return False


def test_raw_psycopg_connect_calls_are_in_explicit_inventory() -> None:
    # Guards the loophole the import scan cannot see: a test may open a real
    # connection with a bare `import psycopg` + `psycopg.connect(...)` and
    # never touch tests.postgres_support. Without the postgres marker the
    # per-test TRUNCATE isolation never runs for it.
    missing: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        if "psycopg" not in source:
            continue
        if not _ast_calls_psycopg_connect(source):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative not in test_config._POSTGRES_TEST_FILES:
            missing.append(relative)

    assert missing == []


def test_postgres_inventory_entries_reference_existing_files() -> None:
    # The inventory is hand-maintained; a deleted/renamed test file leaves a
    # dead entry behind that nothing notices (five accumulated before this
    # guard). Dead entries also hide the missing-direction check above: a
    # renamed file counts as "missing" while its old path sits in the set.
    stale = [
        entry for entry in sorted(test_config._POSTGRES_TEST_FILES) if not (ROOT / entry).exists()
    ]

    assert stale == []


def test_smoke_tier_entries_reference_existing_files() -> None:
    stale = [
        entry for entry in sorted(test_config._SMOKE_TEST_FILES) if not (ROOT / entry).exists()
    ]

    assert stale == []


GUARD_FIXTURE = "_assert_shared_app_invariants"


def _autouse_fixtures(tree: ast.Module) -> dict[str, list[str]]:
    """Map autouse fixture name -> its parameter names, from the conftest AST."""
    fixtures: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and func.attr == "fixture"):
                continue
            if any(
                kw.arg == "autouse" and getattr(kw.value, "value", False)
                for kw in decorator.keywords
            ):
                fixtures[node.name] = [arg.arg for arg in node.args.args]
    return fixtures


def test_shared_app_guard_is_first_autouse_fixture() -> None:
    # The shared-app invariant guard must be torn down after the monkeypatch
    # undo (it observes post-undo app.state), i.e. it must be the first
    # autouse fixture set up. This used to hold only through alphabetical
    # fixture-name sorting — renaming the guard silently broke it. Now every
    # other autouse fixture in the root conftest declares the guard as its
    # first parameter, and this check keeps that structure in place.
    source = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    fixtures = _autouse_fixtures(ast.parse(source))

    assert GUARD_FIXTURE in fixtures, "shared app invariant guard fixture is gone"
    assert "monkeypatch" not in fixtures[GUARD_FIXTURE], (
        "the guard must not request monkeypatch: a dependency would tear it "
        "down BEFORE the monkeypatch undo, inverting the required order"
    )
    for name, params in sorted(fixtures.items()):
        if name == GUARD_FIXTURE:
            continue
        assert params[:1] == [GUARD_FIXTURE], (
            f"autouse fixture {name} must declare {GUARD_FIXTURE} as its first "
            "parameter so the guard teardown runs after it"
        )


def test_database_backed_root_fixtures_are_classified() -> None:
    assert {
        "anon_client",
        "app_factory",
        "client",
        "client_factory",
        "job_db",
        "queries",
        "repo_a",
        "repo_b",
        "tmp_db",
    } <= test_config._POSTGRES_FIXTURES


def test_quality_gate_runs_unit_and_postgres_layers_with_combined_coverage() -> None:
    workflow = (ROOT / ".github/workflows/quality-gate.yml").read_text(encoding="utf-8")

    assert "Backend unit tests (PostgreSQL offline)" in workflow
    assert "AGENT_LEGION_TEST_RESULT_NAME: backend-unit" in workflow
    assert "GATE_TIER: unit" in workflow
    assert "backend-unit-junit.xml" in workflow
    # Phase 5C-2/5C-3: the postgres tier is hash-sharded into three parallel
    # jobs (aggregator A hosts api:check + the combined floor; B also runs the
    # tests/full gate; C runs only its shard), each on its own COVERAGE_FILE
    # and result name.
    assert "backend-postgres-a:" in workflow
    assert "backend-postgres-b:" in workflow
    assert "backend-postgres-c:" in workflow
    assert "Backend PostgreSQL tests (shard 1/3)" in workflow
    assert "Backend PostgreSQL tests (shard 2/3)" in workflow
    assert "Backend PostgreSQL tests (shard 3/3)" in workflow
    assert "GATE_TIER: postgres" in workflow
    assert "GATE_SHARD: 1/3" in workflow
    assert "GATE_SHARD: 2/3" in workflow
    assert "GATE_SHARD: 3/3" in workflow
    assert "AGENT_LEGION_TEST_RESULT_NAME: backend-postgres-a" in workflow
    assert "AGENT_LEGION_TEST_RESULT_NAME: backend-postgres-b" in workflow
    assert "AGENT_LEGION_TEST_RESULT_NAME: backend-postgres-c" in workflow
    assert "backend-postgres-a-junit.xml" in workflow
    assert "backend-postgres-b-junit.xml" in workflow
    assert "backend-postgres-c-junit.xml" in workflow
    # 5C-3: the aggregator can reach the merge before its peers upload under
    # load (run 30811145691), so it polls `gh api` for the peer coverage
    # artifacts first (actions: read) and only then downloads them; a missing
    # shard still fails the explicit download step.
    assert "Wait for peer shard coverage artifacts" in workflow
    assert "actions: read" in workflow
    # Phase 5C: the tiers run as parallel jobs, each writing an independent
    # coverage data file (no cross-tier AGENT_LEGION_COV_APPEND). The
    # backend-postgres-a job downloads every shard's artifact and merges all
    # shards via `coverage combine` before the single 85% floor check.
    assert "COVERAGE_FILE: coverage-data/backend-unit.coverage" in workflow
    assert "COVERAGE_FILE: coverage-data/backend-postgres-a.coverage" in workflow
    assert "COVERAGE_FILE: coverage-data/backend-postgres-b.coverage" in workflow
    assert "COVERAGE_FILE: coverage-data/backend-postgres-c.coverage" in workflow
    assert "COVERAGE_FILE: coverage-data/backend-full.coverage" in workflow
    assert "name: backend-unit-coverage" in workflow
    assert "name: backend-postgres-b-coverage" in workflow
    assert "name: backend-postgres-c-coverage" in workflow
    assert "coverage combine" in workflow
    # Every pytest shard (tiers and the tests/full gate) runs on its own
    # COVERAGE_FILE, so each must defer the 85% floor to the combined report;
    # a shard enforcing it on partial data fails at ~59%. The tier shards get
    # --cov-fail-under=0 from AGENT_LEGION_COV=1 in check-quick-backend.sh;
    # the tests/full step sets it inline.
    full_gate_step = workflow.split("Full gate evidence (tests/full)", 1)[1]
    full_gate_step = full_gate_step.split("- name:", 1)[0]
    assert "--cov-fail-under=0" in full_gate_step


def test_test_harness_skips_module_level_application_bootstrap() -> None:
    # The module-level `app = create_app(start_worker=True)` was removed in
    # favor of the create_prod_app factory: importing server.app.main must be
    # side-effect free by construction, not by a conftest env escape hatch.
    conftest_source = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "AGENT_LEGION_SKIP_MODULE_APP" not in conftest_source
    main_source = (ROOT / "server/app/main.py").read_text(encoding="utf-8")
    assert "AGENT_LEGION_SKIP_MODULE_APP" not in main_source
    # No module-level app assignment may reappear (import-time startup);
    # assignments inside function bodies (create_app itself) are fine.
    for node in ast.parse(main_source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "app":
                    raise AssertionError(
                        "module-level `app = ...` reintroduced; use create_prod_app factory"
                    )


@pytest.mark.no_db
def test_importing_main_module_has_no_side_effects() -> None:
    # The factory refactor made `import server.app.main` inert: no module
    # attribute `app` may exist, and importing must not construct an app.
    import server.app.main as main_module

    assert not hasattr(main_module, "app")
    assert hasattr(main_module, "create_prod_app")
    assert hasattr(main_module, "create_app")
