"""External artifact-access routes (#631): the status + manifest listing
read surfaces.

Split from test_external_artifacts.py when it crossed the 800-line
test-file budget (#779 codex train review P1-3); cases migrated verbatim.
Shared seeding lives in external_artifact_testlib.py / the directory
conftest (``two_workspaces``). See the sibling files for the raw download,
local-fallback and access-control families.
"""

from __future__ import annotations

import gzip
import hashlib

from tests.routes.jobs.external_artifact_testlib import _register_object_artifact

# --- 状态端点 ----------------------------------------------------------------


def test_status_returns_lightweight_view(two_workspaces):
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_a["id"]
    assert body["workspace_id"] == "ws-a"
    assert body["status"] in {"queued", "running", "completed", "failed", "cancelled"}
    assert body["artifacts"] == []  # nothing produced yet
    # contract: exactly the lightweight fields, no node_summaries/storage_dir.
    assert set(body) == {
        "job_id",
        "workspace_id",
        "status",
        "outcome",
        "created_at",
        "updated_at",
        "error_summary",
        "completed_nodes",
        "total_nodes",
        "artifacts",
    }


def test_status_lists_artifact_names_once_produced(two_workspaces):
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "report.json", b'{"r": 1}')

    body = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()

    assert body["artifacts"] == ["report.json"]


# --- 清单端点 ----------------------------------------------------------------


def test_artifact_list_object_rows_carry_execution_metadata(two_workspaces):
    c, job_a, _ = two_workspaces
    payload = b'{"result": 1}'
    _register_object_artifact(c, job_a, "report.json", payload)
    _register_object_artifact(c, job_a, "frame.png", b"\x89PNG-bytes")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_a["id"]
    assert body["workspace_id"] == "ws-a"
    assert body["object_storage_enabled"] is True
    entries = {entry["name"]: entry for entry in body["artifacts"]}
    report = entries["report.json"]
    assert report["storage"] == "object"
    assert report["node_key"] == "upstream"
    assert report["size_bytes"] == len(gzip.compress(payload))  # stored size (#338)
    assert report["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert report["uploaded_at"] is not None  # distinguishes the execution (#508)
    # media_type mirrors the raw endpoint's whitelist: non-whitelisted
    # extensions download as octet-stream.
    assert report["media_type"] == "application/octet-stream"
    assert entries["frame.png"]["media_type"] == "image/png"


def test_artifact_list_reflects_rerun_latest_row(two_workspaces):
    """#508 rerun semantics: the listing always answers the CURRENT row —
    a re-uploaded artifact replaces the entry (one per name) and its
    content_hash/uploaded_at identify the execution that produced it."""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "report.json", b'{"v": 1}', node_key="node_a")
    _register_object_artifact(c, job_a, "report.json", b'{"v": 2}', node_key="node_a")

    body = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()

    entries = [e for e in body["artifacts"] if e["name"] == "report.json"]
    assert len(entries) == 1
    assert entries[0]["content_hash"] == hashlib.sha256(b'{"v": 2}').hexdigest()


def test_artifact_list_unfinished_job_is_empty_not_404(two_workspaces):
    """Stable not-ready semantics: a running job returns an empty list plus
    its status — external pollers key off status, not 404s."""
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["artifacts"] == []
    assert body["status"] in {"queued", "running", "completed", "failed", "cancelled"}
