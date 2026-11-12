"""Batch selection has no item cap; the old env only warns (#1362)."""

import logging
import runpy

import pytest

from server.app.services import job_selection_resolver
from server.app.services.job_selection_resolver import MAX_BATCH_JOBS_ENV

pytestmark = pytest.mark.no_db


def test_retired_cap_api_is_gone() -> None:
    assert not hasattr(job_selection_resolver, "MAX_BATCH_JOBS")
    assert not hasattr(job_selection_resolver, "ensure_batch_size")
    assert not hasattr(job_selection_resolver, "BatchSelectionTooLargeError")


def test_old_env_warns_and_does_not_restore_cap(monkeypatch, caplog) -> None:
    monkeypatch.setenv(MAX_BATCH_JOBS_ENV, "1")
    with caplog.at_level(logging.WARNING, logger=job_selection_resolver.__name__):
        namespace = runpy.run_path(str(job_selection_resolver.__file__))
    assert "deprecated and ignored" in caplog.text
    assert "MAX_BATCH_JOBS" not in namespace


def test_unset_old_env_does_not_warn(monkeypatch, caplog) -> None:
    monkeypatch.delenv(MAX_BATCH_JOBS_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger=job_selection_resolver.__name__):
        runpy.run_path(str(job_selection_resolver.__file__))
    assert caplog.text == ""
