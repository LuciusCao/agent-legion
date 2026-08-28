"""Unit tests for scripts/pytest_aff_selection.py (affected-test selection).

These cover the pure logic: coverage-context extraction from a synthetic
SQLite file, repo-relative path mapping, and conservative selection
semantics (unknown test files run wholesale; mapped files union their
recorded tests). End-to-end behavior of the gate tiers lives in
tests/scripts/test_quality_gate_scripts.py.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from scripts.pytest_aff_selection import (
    build_index_from_coverage,
    select_affected_tests,
)

_REPO_ROOT = "/repo"

pytestmark = pytest.mark.no_db


def _write_coverage_db(path, contexts: dict[str, list[str]]) -> None:
    """Create a minimal coverage SQLite file: file table + context table +
    line_bits rows linking them (the real schema the extractor reads)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table file (id integer primary key, path text);
        create table context (id integer primary key, context text);
        create table line_bits (file_id integer, context_id integer, numbits blob);
        create table arc (file_id integer, context_id integer, fromno integer, tono integer);
        """
    )
    file_ids: dict[str, int] = {}
    context_ids: dict[str, int] = {}
    for source, nodeids in contexts.items():
        if source not in file_ids:
            conn.execute("insert into file(path) values (?)", (source,))
            file_ids[source] = conn.execute("select last_insert_rowid()").fetchone()[0]
        for nodeid in nodeids:
            context = f"{nodeid}|run"
            if context not in context_ids:
                conn.execute("insert into context(context) values (?)", (context,))
                context_ids[context] = conn.execute("select last_insert_rowid()").fetchone()[0]
            conn.execute(
                "insert into line_bits(file_id, context_id, numbits) values (?, ?, x'00')",
                (file_ids[source], context_ids[context]),
            )
    conn.commit()
    conn.close()


def test_build_index_maps_repo_files_to_nodeids(tmp_path):
    coverage = tmp_path / ".coverage"
    _write_coverage_db(
        coverage,
        {
            "/repo/server/app/settings.py": [
                "tests/test_settings.py::test_a",
                "tests/test_settings.py::test_b",
            ],
            "/repo/tests/test_settings.py": ["tests/test_settings.py::test_a"],
            "/elsewhere/venv/lib.py": ["tests/test_settings.py::test_a"],
        },
    )

    mapping = build_index_from_coverage(coverage, repo_root=pathlib.Path(_REPO_ROOT))

    # venv paths are dropped; repo paths keep their nodeids.
    assert mapping == {
        "server/app/settings.py": [
            "tests/test_settings.py::test_a",
            "tests/test_settings.py::test_b",
        ],
        "tests/test_settings.py": ["tests/test_settings.py::test_a"],
    }


def test_build_index_ignores_non_test_contexts(tmp_path):
    coverage = tmp_path / ".coverage"
    _write_coverage_db(coverage, {})
    conn = sqlite3.connect(coverage)
    conn.execute("insert into file(path) values ('/repo/server/app/settings.py')")
    file_id = conn.execute("select last_insert_rowid()").fetchone()[0]
    for context in ("", "tests/test_x.py::test_a|run", "not-a-test|run", "setup|run"):
        conn.execute("insert into context(context) values (?)", (context,))
        context_id = conn.execute("select last_insert_rowid()").fetchone()[0]
        conn.execute(
            "insert into line_bits(file_id, context_id, numbits) values (?, ?, x'00')",
            (file_id, context_id),
        )
    conn.commit()
    conn.close()

    mapping = build_index_from_coverage(coverage, repo_root=pathlib.Path(_REPO_ROOT))

    # `setup|run` and `not-a-test|run` lack a `::` nodeid split and are dropped;
    # only the genuine test context survives.
    assert mapping == {"server/app/settings.py": ["tests/test_x.py::test_a"]}


def test_select_affected_tests_unions_covering_tests():
    mapping = {
        "server/app/settings.py": ["tests/test_settings.py::test_a"],
        "server/app/jobs.py": ["tests/test_jobs.py::test_b"],
        "tests/test_settings.py": ["tests/test_settings.py::test_a"],
    }

    selected = select_affected_tests(["server/app/settings.py"], mapping)

    assert selected == ["tests/test_settings.py::test_a"]


def test_select_affected_tests_runs_new_test_files_wholesale(tmp_path):
    mapping = {"server/app/settings.py": ["tests/test_settings.py::test_a"]}
    new_test = tmp_path / "tests" / "test_new.py"
    new_test.parent.mkdir(parents=True)
    new_test.write_text("def test_new():\n    pass\n", encoding="utf-8")

    # A test file absent from the index (new, or never covered) must run all
    # of its tests — selection is a conservative superset.
    selected = select_affected_tests(["tests/test_new.py"], mapping, repo_root=tmp_path)

    assert selected == ["tests/test_new.py"]


def test_select_affected_tests_drops_nodeids_of_deleted_test_files(tmp_path):
    mapping = {
        "tests/test_deleted.py": [
            "tests/test_deleted.py::test_a",
            "tests/test_deleted.py::test_b",
        ],
        "server/app/settings.py": ["tests/test_alive.py::test_c"],
    }
    alive = tmp_path / "tests" / "test_alive.py"
    alive.parent.mkdir(parents=True)
    alive.write_text("def test_c():\n    pass\n", encoding="utf-8")

    # The deleted test file still has index records (the indexer ran it
    # before the deletion), but every recorded nodeid now points at a file
    # that no longer exists; passing any of them to pytest would fail
    # collection for as long as the deletion sits in the diff.
    selected = select_affected_tests(
        ["tests/test_deleted.py", "server/app/settings.py"],
        mapping,
        repo_root=tmp_path,
    )

    assert selected == ["tests/test_alive.py::test_c"]


def test_select_affected_tests_unknown_source_maps_to_nothing():
    mapping = {"server/app/settings.py": ["tests/test_settings.py::test_a"]}

    # A changed source file with no coverage record (e.g. a brand-new module)
    # selects nothing by itself — its tests do not exist in the index yet, and
    # the fallback heuristics of the gate tier catch brand-new files via the
    # tests/ prefix rule above.
    assert select_affected_tests(["server/app/new_module.py"], mapping) == []


def test_select_affected_tests_sorted_and_deduplicated(tmp_path):
    mapping = {
        "server/app/a.py": ["tests/test_x.py::test_2", "tests/test_x.py::test_1"],
        "server/app/b.py": ["tests/test_x.py::test_1"],
    }
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_1():\n    pass\n\n\ndef test_2():\n    pass\n", encoding="utf-8")

    selected = select_affected_tests(
        ["server/app/b.py", "server/app/a.py"], mapping, repo_root=tmp_path
    )

    assert selected == ["tests/test_x.py::test_1", "tests/test_x.py::test_2"]
