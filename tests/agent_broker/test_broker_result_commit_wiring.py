"""Broker result-commit routing keeps its optional batcher contract."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from server.app.agent_broker import AgentExecutionBroker

pytestmark = pytest.mark.no_db


class _Batcher:
    def __init__(self, *, use_direct: bool = False) -> None:
        self.use_direct = use_direct
        self.submissions = []

    def submit(self, kind, args, *, direct):
        self.submissions.append((kind, args, direct))
        return direct() if self.use_direct else "batched-lease"


def _broker(batcher) -> tuple[AgentExecutionBroker, Mock]:
    broker = object.__new__(AgentExecutionBroker)
    broker.result_batcher = batcher
    direct = Mock(return_value="direct-lease")
    broker._mark_done_direct = direct
    return broker, direct


def test_mark_done_without_batcher_keeps_direct_kill_switch() -> None:
    broker, direct = _broker(None)
    outcome = {"status": "completed"}

    assert broker.mark_done("exec-1", "worker-1", "lease-1", outcome) == "direct-lease"
    direct.assert_called_once_with("exec-1", "worker-1", "lease-1", outcome)


def test_mark_done_submits_same_write_to_optional_batcher() -> None:
    batcher = _Batcher()
    broker, direct = _broker(batcher)
    outcome = {"status": "completed"}

    assert broker.mark_done("exec-1", "worker-1", "lease-1", outcome) == "batched-lease"
    assert len(batcher.submissions) == 1
    kind, args, _ = batcher.submissions[0]
    assert kind == "mark_done"
    assert args == ("exec-1", "worker-1", "lease-1", outcome)
    direct.assert_not_called()


def test_mark_done_post_stop_fallback_runs_direct_write() -> None:
    batcher = _Batcher(use_direct=True)
    broker, direct = _broker(batcher)
    outcome = {"status": "completed"}

    assert broker.mark_done("exec-1", "worker-1", "lease-1", outcome) == "direct-lease"
    direct.assert_called_once_with("exec-1", "worker-1", "lease-1", outcome)
