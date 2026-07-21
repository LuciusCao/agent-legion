from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def _importer() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "import-sqlite-to-postgres.py"
    spec = importlib.util.spec_from_file_location("sqlite_postgres_importer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load SQLite importer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table workspaces(id text primary key, name text not null);
            create table jobs(
              id text primary key, workspace_id text not null, workflow_key text not null,
              source_type text not null, source_id text not null
            );
            create table job_nodes(
              id integer primary key autoincrement, job_id text not null, node_key text not null
            );
            insert into workspaces(id, name) values ('workspace-1', 'Workspace 1');
            insert into jobs(id, workspace_id, workflow_key, source_type, source_id)
              values ('job-1', 'workspace-1', 'question', 'question', 'source-1');
            insert into job_nodes(id, job_id, node_key) values (41, 'job-1', 'generate');
            """
        )


def test_import_preserves_rows_and_identity_ids(tmp_path: Path) -> None:
    source = tmp_path / "legacy.sqlite"
    _source(source)

    counts = _importer().import_database(source, TEST_DATABASE_URL, truncate=False)

    assert counts["workspaces"] == 1
    assert counts["jobs"] == 1
    assert counts["job_nodes"] == 1
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select id, node_key from job_nodes where job_id=?", ("job-1",)
        ).fetchone()
    assert row == {"id": 41, "node_key": "generate"}


def test_import_refuses_a_populated_target(tmp_path: Path) -> None:
    source = tmp_path / "legacy.sqlite"
    _source(source)
    importer = _importer()
    importer.import_database(source, TEST_DATABASE_URL, truncate=False)

    with pytest.raises(RuntimeError, match="target PostgreSQL database is not empty"):
        importer.import_database(source, TEST_DATABASE_URL, truncate=False)
