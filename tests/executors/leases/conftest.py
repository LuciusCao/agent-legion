from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "jobs.sqlite"


@pytest.fixture
def queries(tmp_db: Path) -> JobQueries:
    return JobQueries(tmp_db, tmp_db.parent / "jobs")


@pytest.fixture
def repo_a(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)


@pytest.fixture
def repo_b(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)
