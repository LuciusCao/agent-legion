"""Terminal result effects stay ordered after both commit steps."""

from __future__ import annotations

from pathlib import Path

import pytest

import server.app.agent_broker.agent_result_commit_effects as result_effects
from server.app.agent_broker.agent_result_commit import commit_agent_result
from server.app.agent_control.completion import AgentOutcome
from server.app.services.runtime_profile import profile

pytestmark = pytest.mark.no_db


class _Broker:
    def __init__(self, bundle_dir: Path, events: list[str]) -> None:
        self.bundle_dir = bundle_dir
        self.database_dsn = None
        self.events = events

    def claimed_payload(self, execution_id: str, worker_id: str):
        return {
            "lease_id": "lease-1",
            "job_id": "job-1",
            "node_key": "node-a",
            "manifest": {"bundle_name": "bundle-1"},
        }

    def mark_done(self, *args) -> str:
        self.events.append("mark_done")
        return "lease-1"

    def discard_result_archive(self, archive_name: str) -> None:
        self.events.append("discard_archive")

    def retire_bundle(self, bundle_name: str) -> None:
        self.events.append("retire_bundle")


class _Completion:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stage_timer = None

    def finish(self, **kwargs) -> bool:
        self.events.append("finish")
        self.stage_timer = kwargs["stage_timer"]
        return True


def test_commit_orders_terminal_writes_before_gauge_and_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    events: list[str] = []
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    staged_body = tmp_path / "staged.tar.gz"
    staged_body.write_bytes(b"staged")
    broker = _Broker(bundle_dir, events)
    completion = _Completion(events)

    monkeypatch.setattr(
        result_effects.worker_events,
        "note_execution_finished",
        lambda *args: events.append("execution_finished"),
    )
    monkeypatch.setattr(profile, "note_execution_done", lambda: events.append("execute_done"))
    monkeypatch.setattr(
        result_effects,
        "report_result_stages",
        lambda timer, **kwargs: events.append(f"report_stages:{kwargs['committed']}"),
    )

    commit_agent_result(
        broker,
        completion,
        "exec-1",
        "worker-1",
        "lease-1",
        AgentOutcome(status="completed", exit_code=0),
        {"status": "completed", "exit_code": 0},
        staged_body,
    )

    assert events == [
        "finish",
        "mark_done",
        "execution_finished",
        "execute_done",
        "report_stages:True",
        "discard_archive",
        "retire_bundle",
    ]
    assert completion.stage_timer is not None
    assert "mark_done" in completion.stage_timer.stages
