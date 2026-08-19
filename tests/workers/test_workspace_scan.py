"""Workspace-driven worker scan list (schema v50, issue #112).

The scan list is built from the workspaces table: one entry per workspace
with a non-empty default_workflow_key, carrying the workspace's ACTIVE
revision definition as the job fallback. reload_scan_entries picks up new
workspaces without a restart.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from server.app.workflow_worker.catalog_scan import load_workflow_scan_entries
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker


def test_scan_entries_cover_workspaces_and_active_revisions(settings, job_db) -> None:
    published = job_db.create_workspace(
        "scan-a", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, published["id"])
    unpublished = job_db.create_workspace("scan-b", default_workflow_key="flow_b")
    job_db.create_workspace("scan-blank", default_workflow_key="")

    entries = load_workflow_scan_entries(settings)

    by_workspace = {workspace_id: (key, definition) for workspace_id, key, definition in entries}
    assert set(by_workspace) == {str(published["id"]), str(unpublished["id"])}
    key, definition = by_workspace[str(published["id"])]
    assert key == "education_video_problems_generation"
    assert definition is not None and definition.key == "education_video_problems_generation"
    key_b, definition_b = by_workspace[str(unpublished["id"])]
    assert key_b == "flow_b"
    assert definition_b is None


def test_reload_scan_entries_picks_up_new_workspaces(tmp_path: Path, settings, job_db) -> None:
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, RecordingExecutor("local-default"), [])
    try:
        worker.reload_scan_entries()
        assert worker._scan_entries == []

        workspace = job_db.create_workspace(
            "scan-hot", default_workflow_key="education_video_problems_generation"
        )
        publish_builtin_revision(job_db, workspace["id"])

        worker.reload_scan_entries()
        by_workspace = {ws: (key, d) for ws, key, d in worker._scan_entries}
        assert str(workspace["id"]) in by_workspace
        key, definition = by_workspace[str(workspace["id"])]
        assert key == "education_video_problems_generation"
        assert definition is not None
    finally:
        worker.stop()


def test_reload_scan_entries_keeps_previous_snapshot_on_failure(
    tmp_path: Path, settings, job_db, monkeypatch
) -> None:
    workspace = job_db.create_workspace(
        "scan-stable", default_workflow_key="education_video_problems_generation"
    )
    publish_builtin_revision(job_db, workspace["id"])
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, RecordingExecutor("local-default"), [])
    try:
        worker.reload_scan_entries()
        snapshot = worker._scan_entries
        assert snapshot

        import server.app.workflow_worker.thread as thread_module

        def _fail(_settings):
            raise RuntimeError("workspaces read failed")

        monkeypatch.setattr(thread_module, "load_workflow_scan_entries", _fail)
        with contextlib.suppress(RuntimeError):
            worker.reload_scan_entries()
        assert worker._scan_entries is snapshot
    finally:
        worker.stop()
