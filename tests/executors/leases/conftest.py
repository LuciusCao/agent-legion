from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    del tmp_path
    return TEST_DATABASE_URL


@pytest.fixture
def queries(tmp_db: str, tmp_path: Path) -> JobQueries:
    return JobQueries(tmp_db, tmp_path / "jobs")


@pytest.fixture
def repo_a(tmp_db: str, queries: JobQueries) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db, data_dir=queries.jobs_dir.parent)


@pytest.fixture
def repo_b(tmp_db: str, queries: JobQueries) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db, data_dir=queries.jobs_dir.parent)
