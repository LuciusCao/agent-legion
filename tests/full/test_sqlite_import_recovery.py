from __future__ import annotations

from pathlib import Path

import pytest

from server.app.db.transaction import read_connection
from tests.db.test_sqlite_import import _importer, _source
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.full_gate


def test_offline_import_can_be_verified_after_reconnect(tmp_path: Path) -> None:
    source = tmp_path / "legacy.sqlite"
    _source(source)
    _importer().import_database(source, TEST_DATABASE_URL, truncate=False)

    with read_connection(TEST_DATABASE_URL) as conn:
        workspace = conn.execute(
            "select id, name from workspaces where id=?", ("workspace-1",)
        ).fetchone()
        job = conn.execute("select id from jobs where id=?", ("job-1",)).fetchone()

    assert workspace == {"id": "workspace-1", "name": "Workspace 1"}
    assert job == {"id": "job-1"}
