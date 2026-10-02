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
from pathlib import Path

import pytest

from server.app.services.job_artifact_objects import JobArtifactObjectStore
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


def test_status_poll_is_unsigned_and_survives_signing_failure(two_workspaces, monkeypatch):
    """#739 codex P2: the status poll is an unsigned name pipeline — N polls
    mint zero presigned URLs (counting stub asserts 0), and a broken signing
    client/credential can no longer 500 the lightweight DB-only status read.
    The control case proves the stub is live and the manifest route signs."""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "clip.mp4", b"0123456789", gzipped=False)
    _register_object_artifact(c, job_a, "report.json", b'{"r": 1}')
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    calls: list[str] = []

    def _boom(storage_key, expires_seconds=3600):  # noqa: ANN001, ANN202
        calls.append(storage_key)
        raise ConnectionError("signing credentials broken")

    monkeypatch.setattr(store.storage, "presign_get", _boom)

    for _ in range(3):
        response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}")
        assert response.status_code == 200
        assert response.json()["artifacts"] == ["clip.mp4", "report.json"]

    assert calls == []

    # Control: the manifest route still reaches presign under the same stub
    # (TestClient re-raises server exceptions, so the boom surfaces here).
    with pytest.raises(ConnectionError, match="signing credentials broken"):
        c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts")
    assert calls


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


# --- #739: presigned download_url -------------------------------------------------


def test_artifact_list_presigns_bare_key_rows(two_workspaces):
    """#739: bare-key object rows carry a presigned download_url +
    expires_at (S3 answers directly — big media downloads leave the Host
    process alone); the raw endpoint stays available as the fallback."""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "clip.mp4", b"0123456789", gzipped=False)
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    storage_key = f"jobs/{job_a['workspace_id']}/{job_a['id']}/clip.mp4"

    body = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()

    entry = next(e for e in body["artifacts"] if e["name"] == "clip.mp4")
    # The signature covers exactly the manifest row's storage_key (no request
    # input beyond job_id/artifact_name can steer it).
    assert store.storage.presigned_gets == [storage_key]
    assert entry["download_url"] == f"https://s3.test/download/{storage_key}"
    assert entry["content_encoding"] == ""
    assert entry["expires_at"] is not None


def test_artifact_list_gzip_rows_carry_no_url(two_workspaces):
    """#338/#739: gzip-stored objects get NO download_url — a presigned GET
    would serve the compressed bytes without the Content-Encoding: gzip header
    the raw endpoint adds, so callers could not tell the stored form apart.
    content_encoding marks the form; the raw endpoint is the only channel."""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "report.json", b'{"r": 1}')  # gzipped=True
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects

    body = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()

    assert store.storage.presigned_gets == []
    entry = next(e for e in body["artifacts"] if e["name"] == "report.json")
    assert entry["download_url"] is None
    assert entry["expires_at"] is None
    assert entry["content_encoding"] == "gzip"
    # The raw endpoint still serves it (Content-Encoding: gzip passthrough —
    # httpx transparently decodes, so the visible content is the JSON itself).
    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/report.json/raw")
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.content == b'{"r": 1}'


def test_artifact_list_local_entries_have_no_url(two_workspaces):
    """local rows and object storage keep download_url/expires_at null —
    there is no object to presign for; the raw endpoint serves them.
    ``script.md`` is a declared output in the job snapshot — the local
    listing is narrowed to declared names (#703 codex round 4)."""
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("old", encoding="utf-8")

    body = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()

    entry = next(e for e in body["artifacts"] if e["name"] == "script.md")
    assert entry["storage"] == "local"
    assert entry["download_url"] is None
    assert entry["expires_at"] is None
    assert entry["content_encoding"] == ""


def test_artifact_list_urls_refresh_per_request(two_workspaces):
    """URLs are minted per request (never persisted): two listings both
    answer fresh presign calls — the client-side expiry rule is re-fetch the
    manifest, and the server honours it by re-signing every time."""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "clip.mp4", b"0123456789", gzipped=False)
    storage_key = f"jobs/{job_a['workspace_id']}/{job_a['id']}/clip.mp4"
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects

    c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()

    assert store.storage.presigned_gets == [storage_key, storage_key]
