from __future__ import annotations

from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from tests.helpers import ensure_legacy_workspace_tables


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    q = JobQueries(db_path, jobs_dir)
    ensure_legacy_workspace_tables(q)
    return q
