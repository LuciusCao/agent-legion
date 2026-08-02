from __future__ import annotations

import ast
from pathlib import Path

from tests import conftest as test_config

ROOT = Path(__file__).resolve().parents[1]
DIRECT_POSTGRES_IMPORTS = (
    "from tests.postgres_support",
    "from server.app.main import create_app",
    "from server.app import main",
    "from scripts.export_openapi import build_openapi_schema",
)


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
    assert 'AGENT_LEGION_COV_APPEND: "1"' in workflow
    assert "GATE_TIER: postgres" in workflow
    assert "backend-unit-junit.xml" in workflow
    assert "backend-postgres-junit.xml" in workflow


def test_test_harness_skips_module_level_application_bootstrap() -> None:
    source = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'os.environ["AGENT_LEGION_SKIP_MODULE_APP"] = "1"' in source
