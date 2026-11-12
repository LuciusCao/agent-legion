"""The direct lease finish retry carries the same result stage timer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.app.executors import _lease_finish_batch, _lease_write_paths
from server.app.executors.models import ExecutionResult

pytestmark = pytest.mark.no_db


def test_direct_finish_retry_passes_stage_timer_on_every_attempt(monkeypatch) -> None:
    timer = object()
    result = ExecutionResult(status="completed", exit_code=0)
    attempts = []

    def finish(repo, lease_id, received_result, *, stage_timer=None):
        attempts.append((repo, lease_id, received_result, stage_timer))
        return len(attempts) == 2

    def retry_twice(operation):
        operation()
        return operation()

    monkeypatch.setattr(_lease_write_paths, "finish", finish)
    monkeypatch.setattr(_lease_finish_batch, "retry_on_database_conflict", retry_twice)
    repo = SimpleNamespace()

    assert _lease_finish_batch.finish_via_batcher(repo, None, "lease-1", result, timer) is True
    assert attempts == [
        (repo, "lease-1", result, timer),
        (repo, "lease-1", result, timer),
    ]
