from __future__ import annotations

from server.app.workflow_worker_thread import resolve_submit_max_workers


def test_explicit_config_wins() -> None:
    assert resolve_submit_max_workers(7, [20]) == 7


def test_default_derives_from_remote_capacity() -> None:
    assert resolve_submit_max_workers(None, [20]) == 10
    assert resolve_submit_max_workers(None, [65]) == 33
    assert resolve_submit_max_workers(None, [6]) == 4
    assert resolve_submit_max_workers(None, []) == 4
