"""Workspace-driven worker scan list (schema v50, issue #112).

The scan list is built from the workspaces table: one entry per workspace
with a non-empty default_workflow_key, carrying the workspace's ACTIVE
revision definition as the job fallback. reload_scan_entries picks up new
workspaces without a restart.
"""

from __future__ import annotations

import contextlib
import json
import uuid
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
        assert worker.state.scan_entries == []

        workspace = job_db.create_workspace(
            "scan-hot", default_workflow_key="education_video_problems_generation"
        )
        publish_builtin_revision(job_db, workspace["id"])

        worker.reload_scan_entries()
        by_workspace = {ws: (key, d) for ws, key, d in worker.state.scan_entries}
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
        snapshot = worker.state.scan_entries
        assert snapshot

        import server.app.workflow_worker.thread as thread_module

        def _fail(_settings):
            raise RuntimeError("workspaces read failed")

        monkeypatch.setattr(thread_module, "load_workflow_scan_entries", _fail)
        with contextlib.suppress(RuntimeError):
            worker.reload_scan_entries()
        assert worker.state.scan_entries is snapshot
    finally:
        worker.stop()


def test_scan_entries_contain_unparsable_active_revision(settings, job_db, caplog) -> None:
    """#204: an active revision that fails definition validation is the
    expected business failure — the workspace still gets a scan entry with
    no fallback definition (WARNING names the parse error), instead of the
    whole scan dying."""
    workspace = job_db.create_workspace("scan-bad", default_workflow_key="broken_flow")
    job_db.create_workflow_revision(
        revision_id=f"rev-{uuid.uuid4().hex[:8]}",
        workspace_id=str(workspace["id"]),
        workflow_key="broken_flow",
        version=1,
        status="active",
        # Valid JSON, but an invalid definition (no nodes) — the exact
        # WorkflowDefinitionError path the worker used to swallow broadly.
        definition_json=json.dumps({"key": "broken_flow", "label": "Broken"}),
        definition_hash="hash-broken",
    )

    with caplog.at_level("WARNING", logger="server.app.workflow_worker.catalog_scan"):
        entries = load_workflow_scan_entries(settings)

    by_workspace = {ws: (key, definition) for ws, key, definition in entries}
    assert str(workspace["id"]) in by_workspace
    key, definition = by_workspace[str(workspace["id"])]
    assert key == "broken_flow"
    assert definition is None
    assert any(
        "failed to parse" in rec.message and "broken_flow" in rec.message for rec in caplog.records
    )


def test_scan_entries_contain_malformed_json_revision(settings, job_db, caplog) -> None:
    """Codex P1 on PR #243: JSONDecodeError is a *sibling* ValueError subclass
    (not covered by WorkflowDefinitionError) — catching only the latter let
    one malformed definition_json row kill load_workflow_scan_entries, and
    with it worker startup and scheduling for every workspace. Malformed JSON
    must hit the same per-workspace degradation as schema violations."""
    workspace = job_db.create_workspace("scan-badjson", default_workflow_key="bad_json_flow")
    job_db.create_workflow_revision(
        revision_id=f"rev-{uuid.uuid4().hex[:8]}",
        workspace_id=str(workspace["id"]),
        workflow_key="bad_json_flow",
        version=1,
        status="active",
        # Not JSON at all — the JSONDecodeError path.
        definition_json="{not valid json",
        definition_hash="hash-badjson",
    )

    with caplog.at_level("WARNING", logger="server.app.workflow_worker.catalog_scan"):
        entries = load_workflow_scan_entries(settings)

    by_workspace = {ws: (key, definition) for ws, key, definition in entries}
    assert str(workspace["id"]) in by_workspace
    key, definition = by_workspace[str(workspace["id"])]
    assert key == "bad_json_flow"
    assert definition is None
    assert any(
        "failed to parse" in rec.message and "bad_json_flow" in rec.message
        for rec in caplog.records
    )


def test_scan_entries_propagate_programming_errors(settings, job_db, monkeypatch) -> None:
    """#204 layering guard: only the definition-validation business failure
    is contained. A genuine programming error while parsing a revision must
    propagate instead of silently dropping the workspace's fallback."""
    workspace = job_db.create_workspace("scan-err", default_workflow_key="error_flow")
    job_db.create_workflow_revision(
        revision_id=f"rev-{uuid.uuid4().hex[:8]}",
        workspace_id=str(workspace["id"]),
        workflow_key="error_flow",
        version=1,
        status="active",
        definition_json=json.dumps({"key": "error_flow", "label": "E", "nodes": {"a": {}}}),
        definition_hash="hash-error",
    )

    import server.app.workflow_worker.catalog_scan as catalog_scan

    def _boom(payload):
        raise TypeError("parser bug")

    monkeypatch.setattr(catalog_scan, "workflow_definition_from_dict", _boom)

    try:
        load_workflow_scan_entries(settings)
    except TypeError:
        pass  # the programming error escaped the scan entry loader, as intended
    else:
        raise AssertionError("programming error was swallowed by the scan entry loader")
