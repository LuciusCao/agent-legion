"""Lifecycle visibility is independent of Kimi's idle prompt loop (#772)."""

import json
import os
from dataclasses import replace

import pytest

from server.app.studio_chat.background_activity import BackgroundActivity
from server.app.studio_chat.kimi_task_snapshot import BackgroundTask
from server.app.studio_chat.kimi_task_store import task_snapshots
from tests.services.studio_background_testlib import write_task

pytestmark = pytest.mark.no_db


def task(status="running", **changes):
    return replace(
        BackgroundTask(
            "agent-1",
            "agent",
            status,
            "Review validators",
            1000,
            None,
            1000,
            1000,
            "",
        ),
        **changes,
    )


def emit(activity, snapshots, now):
    events = activity.updates(snapshots, now)
    for event in events:
        activity.recorded(event)
    return events


def test_running_quiet_recovery_and_terminal_with_description_duration_summary():
    activity = BackgroundActivity()
    running = task()
    started = emit(activity, {"agent-1": running}, 1000)
    assert started[0]["status"] == "running"
    assert started[0]["description"] == "Review validators"
    assert "开始" in started[0]["detail"]
    assert emit(activity, {"agent-1": running}, 1001) == []
    alive = replace(running, heartbeat_at=1121)
    quiet = emit(activity, {"agent-1": alive}, 1121)
    assert quiet[0]["status"] == "quiet"
    assert quiet[0]["task_status"] == "running"
    assert emit(activity, {"agent-1": alive}, 1122) == []
    active = replace(alive, output_changed_at=1123)
    assert emit(activity, {"agent-1": active}, 1123)[0]["status"] == "running"
    done = replace(active, status="completed", finished_at=1150, summary="All checks passed")
    finished = emit(activity, {"agent-1": done}, 1200)[0]
    assert finished["event"] == "background_task_finished"
    assert finished["elapsed_seconds"] == 150
    assert "All checks passed" in finished["detail"]
    assert not activity.tasks


def test_missing_or_stale_metadata_warns_without_claiming_failure():
    activity = BackgroundActivity()
    emit(activity, {"agent-1": task()}, 1000)
    assert emit(activity, {}, 1100) == []
    missing = emit(activity, {}, 1121)
    assert missing[0]["status"] == "unavailable"
    assert missing[0]["task_status"] == "running"
    assert emit(activity, {}, 1122) == []
    stale = emit(activity, {"agent-1": task()}, 1123)
    assert stale[0]["status"] == "stale"
    assert stale[0]["event"] != "background_task_finished"


def test_unsaved_event_is_retried_and_approval_does_not_claim_quiet():
    activity = BackgroundActivity()
    snapshots = {"agent-1": task("awaiting_approval", heartbeat_at=1300)}
    pending = activity.updates(snapshots, 1300)
    assert pending[0]["status"] == "awaiting_approval"
    assert activity.updates(snapshots, 1301)
    activity.recorded(pending[0])
    assert activity.updates(snapshots, 1302) == []


@pytest.mark.parametrize("kind", ["agent", "bash"])
@pytest.mark.parametrize(
    "status", ["running", "awaiting_approval", "completed", "failed", "killed", "lost"]
)
def test_reads_supported_lifecycle_fields_with_bounded_terminal_output(tmp_path, kind, status):
    path = write_task(
        tmp_path, status=status, kind=kind, description="Review " * 100, created_at=1000
    )
    runtime = {"status": status, "started_at": 1010, "finished_at": 1100, "heartbeat_at": 1090}
    (path / "runtime.json").write_text(json.dumps(runtime))
    (path / "output.log").write_text("x" * 10000 + "Result summary")
    snapshot = task_snapshots(tmp_path, "acp-1")["agent-1"]
    assert snapshot.kind == kind
    assert len(snapshot.description) == 240
    assert snapshot.started_at == 1010
    assert snapshot.finished_at == 1100
    if snapshot.terminal:
        assert len(snapshot.summary) <= 600
        assert snapshot.summary.endswith("Result summary")
    else:
        assert snapshot.summary == ""


def test_timeout_and_failure_reason_are_in_receipt(tmp_path):
    path = write_task(tmp_path, status="failed", description="Slow check")
    (path / "runtime.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "timed_out": True,
                "failure_reason": "Exceeded deadline",
                "started_at": 1000,
                "finished_at": 1300,
            }
        )
    )
    snapshots = task_snapshots(tmp_path, "acp-1")
    receipt = emit(BackgroundActivity(), snapshots, 1400)[0]
    assert receipt["status"] == "timed_out"
    assert receipt["elapsed_seconds"] == 300
    assert "Exceeded deadline" in receipt["detail"]


@pytest.mark.parametrize("file", ["spec.json", "runtime.json", "output.log"])
def test_special_files_and_symlinks_cannot_hang_reader(tmp_path, file):
    path = write_task(tmp_path, status="completed")
    target = path / file
    if target.exists():
        target.unlink()
    os.mkfifo(target)
    result = task_snapshots(tmp_path, "acp-1")
    assert result["agent-1"].summary == "" if file == "output.log" else not result
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text('{"status":"completed"}')
    target.symlink_to(outside)
    result = task_snapshots(tmp_path, "acp-1")
    assert result["agent-1"].summary == "" if file == "output.log" else not result


def test_nonfinite_timestamps_unknown_state_and_child_tasks_are_ignored(tmp_path):
    path = write_task(tmp_path, status="running")
    (path / "runtime.json").write_text(
        '{"status":"running","started_at":NaN,"heartbeat_at":Infinity}'
    )
    snapshot = task_snapshots(tmp_path, "acp-1")["agent-1"]
    assert snapshot.started_at is None
    assert snapshot.heartbeat_at is None
    write_task(tmp_path, "agent-2", "unknown")
    write_task(tmp_path, "agent-3", "running", owner_role="subagent")
    assert set(task_snapshots(tmp_path, "acp-1")) == {"agent-1"}


def test_huge_timestamp_and_foreign_output_link_do_not_block_other_tasks(tmp_path):
    path = write_task(tmp_path, "agent-1", status="completed")
    (path / "runtime.json").write_text(json.dumps({"status": "completed", "started_at": 10**500}))
    foreign = tmp_path / "secret"
    foreign.write_text("foreign result")
    os.link(foreign, path / "output.log")
    write_task(tmp_path, "agent-2", status="running")
    snapshots = task_snapshots(tmp_path, "acp-1")
    assert snapshots["agent-1"].started_at is None
    assert snapshots["agent-1"].summary == ""
    assert snapshots["agent-2"].status == "running"
