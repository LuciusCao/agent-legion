"""Agent-plane result batcher construction and late-arm wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.app.agent_broker.mark_done_batch import mark_done_many_with_retry
from server.app.agent_broker.result_commit_batcher import ResultCommitBatcher
from server.app.agent_broker.result_commit_plane import (
    bind_result_commit_arms,
    build_result_commit_batcher,
)
from server.app.executors._lease_finish_batch import finish_many_with_retry

pytestmark = pytest.mark.no_db


def _settings(enabled: bool):
    return SimpleNamespace(
        executor_runtime=SimpleNamespace(
            agent_workers=SimpleNamespace(result_commit_batching=enabled)
        )
    )


def test_batcher_requires_setting_and_app_lifecycle_gate() -> None:
    assert build_result_commit_batcher(_settings(False), result_batching=True) is None
    assert build_result_commit_batcher(_settings(True), result_batching=False) is None
    assert isinstance(
        build_result_commit_batcher(_settings(True), result_batching=True), ResultCommitBatcher
    )


def test_batcher_binds_both_retry_wrapped_arms_to_their_owners() -> None:
    batcher = ResultCommitBatcher(None, None)
    broker = object()
    executor_leases = object()

    bind_result_commit_arms(batcher, broker=broker, executor_leases=executor_leases)

    assert batcher.finish_many.func is finish_many_with_retry
    assert batcher.finish_many.args == (executor_leases,)
    assert batcher.mark_done_many.func is mark_done_many_with_retry
    assert batcher.mark_done_many.args == (broker,)
    assert batcher._thread is None
