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


@pytest.mark.parametrize(("database_exists", "expected_execution_count"), [(False, 3), (True, 2)])
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
    assert "Backend PostgreSQL tests" in workflow
    assert "AGENT_LEGION_TEST_RESULT_NAME: backend-postgres" in workflow
    assert "GATE_TIER: postgres" in workflow
    assert "backend-unit-junit.xml" in workflow
    assert "backend-postgres-junit.xml" in workflow
    # Phase 5C: the tiers run as parallel jobs, each writing an independent
    # coverage data file (no cross-tier AGENT_LEGION_COV_APPEND). The
    # backend-postgres job downloads the unit shard's artifact and merges all
    # shards via `coverage combine` before the single 85% floor check.
    assert "COVERAGE_FILE: coverage-data/backend-unit.coverage" in workflow
    assert "COVERAGE_FILE: coverage-data/backend-postgres.coverage" in workflow
    assert "name: backend-unit-coverage" in workflow
    assert "coverage combine" in workflow
    # Every pytest shard (tiers and the tests/full gate) runs on its own
    # COVERAGE_FILE, so each must defer the 85% floor to the combined report;
    # a shard enforcing it on partial data fails at ~59%.
    full_gate_step = workflow.split("Full gate evidence (tests/full)", 1)[1]
    full_gate_step = full_gate_step.split("- name:", 1)[0]
    assert "--cov-fail-under=0" in full_gate_step


def test_test_harness_skips_module_level_application_bootstrap() -> None:
    source = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'os.environ["AGENT_LEGION_SKIP_MODULE_APP"] = "1"' in source
