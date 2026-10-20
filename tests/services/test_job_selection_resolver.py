"""MAX_BATCH_JOBS env override (#1348): parsing, fallback, and clamping."""

import pytest

from server.app.jobs.queries.job_bulk_sql import CHUNK_ROWS
from server.app.services import job_selection_resolver
from server.app.services.job_selection_resolver import (
    DEFAULT_MAX_BATCH_JOBS,
    MAX_BATCH_JOBS_ENV,
)

pytestmark = pytest.mark.no_db


def test_default_cap_is_20000_and_chunk_aligned() -> None:
    assert DEFAULT_MAX_BATCH_JOBS == 20000
    assert DEFAULT_MAX_BATCH_JOBS % CHUNK_ROWS == 0


def test_env_unset_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv(MAX_BATCH_JOBS_ENV, raising=False)
    assert job_selection_resolver._max_batch_jobs() == DEFAULT_MAX_BATCH_JOBS


def test_env_override_is_respected(monkeypatch) -> None:
    monkeypatch.setenv(MAX_BATCH_JOBS_ENV, "50000")
    assert job_selection_resolver._max_batch_jobs() == 50000


def test_env_invalid_value_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv(MAX_BATCH_JOBS_ENV, "not-a-number")
    assert job_selection_resolver._max_batch_jobs() == DEFAULT_MAX_BATCH_JOBS


@pytest.mark.parametrize("value", ["0", "-3"])
def test_env_non_positive_clamps_to_one(monkeypatch, value: str) -> None:
    monkeypatch.setenv(MAX_BATCH_JOBS_ENV, value)
    assert job_selection_resolver._max_batch_jobs() == 1
