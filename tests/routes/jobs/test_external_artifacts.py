"""External artifact-access routes (#631): submit → poll → download loop.

The three workspace-prefixed endpoints mount in job_group at app build time
(routes/__init__.py → job_route_group), so these tests exercise the production
wiring end-to-end: require_workspace_access (the admin session passes; the
#626 workspace API token plugs into the same guard), the explicit
job.workspace_id comparison (cross-workspace is 404, not 403 — no id
enumeration), manifest fields with execution-distinguishing metadata
(content_hash / uploaded_at, #508), and raw download integrity for both
binary and text artifacts across both storage branches.

Object-storage tests swap ``app.state.job_artifact_objects.storage`` in place
(monkeypatch, auto-restored): the store instance is shared by every service
the routes read through, so a single attribute swap enables the authoritative
branch without rebuilding the app (same pattern as test_runs_bundle_api's
materials_service.storage swap).
"""

from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path

import pytest

from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage


def _seed_workspace(c, workspace_id: str) -> None:
    from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions

    c.post("/api/workspaces", json={"id": workspace_id, "name": workspace_id})
    seed_workspace_agent_definitions(workspace_id)
    publish_legacy_intake_revision(c.app.state.job_db, workspace_id)


def _create_job(c, workspace_id: str, source_id: str = "Q003") -> dict:
    created = c.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": workspace_id,
            "source_kind": "direct_ids",
            "knowledge_point_ids": [source_id],
        },
    ).json()
    return created["jobs"][0]


@pytest.fixture
def two_workspaces(client_factory, monkeypatch):
    """ws-a and ws-b with one job each, object storage enabled via the
    shared store instance — the cross-workspace probe setup."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
        _seed_workspace(c, "ws-a")
        _seed_workspace(c, "ws-b")
        job_a = _create_job(c, "ws-a")
        job_b = _create_job(c, "ws-b")
        yield c, job_a, job_b


def _register_object_artifact(
    c, job: dict, name: str, payload: bytes, *, node_key: str = "upstream", gzipped: bool = True
) -> None:
    """Register an authority-copy manifest row the way the Worker-direct
    channel does (HEAD-verified record_remote), storing the given bytes."""
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    stored = gzip.compress(payload) if gzipped else payload
    storage_key = f"jobs/{job['workspace_id']}/{job['id']}/{name}"
    if gzipped:
        storage_key += ".gz"
    store.storage.objects[storage_key] = stored
    store.record_remote(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key=node_key,
        name=name,
        storage_key=storage_key,
        size_bytes=len(stored),
        content_hash=hashlib.sha256(payload).hexdigest(),
    )


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


def test_status_cross_workspace_is_404(two_workspaces):
    c, _, job_b = two_workspaces

    # ws-a member probing a ws-b job id: 404 (not 403), no enumeration.
    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}")

    assert response.status_code == 404


def test_status_unknown_job_is_404(two_workspaces):
    c, _, _ = two_workspaces

    assert c.get("/api/workspaces/ws-a/jobs/missing").status_code == 404


def test_status_unknown_workspace_is_404(two_workspaces):
    c, _, _ = two_workspaces

    # require_workspace_access rejects the non-member workspace first.
    assert c.get("/api/workspaces/ws-z/jobs/whatever").status_code == 404


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


def test_artifact_list_local_only_names_without_object_store(client_factory, monkeypatch):
    """Instance without a bucket: job_dir names still list (storage=local,
    no manifest metadata), object_storage_enabled flags the degradation."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "result.json").write_text("{}", encoding="utf-8")

        response = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["object_storage_enabled"] is False
    entry = next(e for e in body["artifacts"] if e["name"] == "result.json")
    assert entry["storage"] == "local"
    assert entry["size_bytes"] is None
    assert entry["content_hash"] == ""
    assert entry["uploaded_at"] is None


def test_artifact_list_unfinished_job_is_empty_not_404(two_workspaces):
    """Stable not-ready semantics: a running job returns an empty list plus
    its status — external pollers key off status, not 404s."""
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["artifacts"] == []
    assert body["status"] in {"queued", "running", "completed", "failed", "cancelled"}


def test_artifact_list_cross_workspace_is_404(two_workspaces):
    c, _, job_b = two_workspaces
    _register_object_artifact(c, job_b, "report.json", b"{}")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}/artifacts")

    assert response.status_code == 404


# --- raw 端点 ----------------------------------------------------------------


def test_raw_downloads_binary_artifact_bytes(two_workspaces):
    """End-to-end binary integrity: PNG magic bytes survive the full
    object-store → gzip → stream → response chain (local cache empty)."""
    c, job_a, _ = two_workspaces
    payload = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40  # ~10 KiB binary
    _register_object_artifact(c, job_a, "frame.png", payload)

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/frame.png/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    # gzip 透明解压：open_stream 分支返回未压缩内容字节。
    assert response.content == payload


def test_raw_downloads_text_artifact_from_local_cache(two_workspaces):
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/result.json/raw")

    assert response.status_code == 200
    # 白名单外扩展名（含 .json/.html）按 raw 白名单策略强制下载。
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.content == b'{"ok": true}'


def test_raw_cross_workspace_is_404(two_workspaces):
    """The key security property: a ws-b artifact is unreadable through the
    ws-a prefix even though the bare /jobs/{job_id} routes exist."""
    c, _, job_b = two_workspaces
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_b['id']}/artifacts/frame.png/raw")

    assert response.status_code == 404


def test_raw_missing_artifact_is_404(two_workspaces):
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/nope.png/raw")

    assert response.status_code == 404


def test_raw_rejects_traversal(two_workspaces):
    """Structural sub-path immunity: ``{artifact_name}`` is a single path
    segment, so any encoded ``/`` (``%2F``) or ``..`` segment either gets
    normalized away by the ASGI stack or fails to match the route — a
    sub-path name can never reach the handler. (The bare
    ``/jobs/{job_id}/artifacts/{artifact_name:path}`` route instead captures
    the whole remainder and rejects in the service with 400.)"""
    c, job_a, _ = two_workspaces

    for name in ("..%2Fagent_legion.sqlite", "%2e%2e%2Fagent_legion.sqlite"):
        response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{name}/raw")
        assert response.status_code == 404


def test_raw_missing_object_is_404(two_workspaces, monkeypatch):
    """Manifest row exists but the object is gone (bucket lifecycle deleted
    it): the store surfaces NoSuchKey, the read path degrades to 404 —
    not a 500 — with no local cache copy to fall back on."""
    c, job_a, _ = two_workspaces
    # The manifest row must exist so open_raw reaches the storage read:
    # without it lookup() returns None and the test would pass vacuously
    # through the plain not-found path (the monkeypatch never running).
    _register_object_artifact(c, job_a, "ghost.json", b"{}")
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    # Simulate the lifecycle delete: the manifest row stays, the object is gone.
    for key in list(store.storage.objects):
        if key.endswith("ghost.json.gz"):
            del store.storage.objects[key]

    def _lifecycle_deleted(key: str) -> io.BytesIO:
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr(store.storage, "open_stream", _lifecycle_deleted)

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/ghost.json/raw")

    assert response.status_code == 404


def test_raw_range_request_on_object_artifact(two_workspaces):
    """External video consumers need byte ranges: 206 + Content-Range on the
    object-store branch (gzip objects ignore Range, so use a bare-key row)."""
    c, job_a, _ = two_workspaces
    payload = b"0123456789"
    _register_object_artifact(c, job_a, "clip.mp4", payload, gzipped=False)

    response = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/clip.mp4/raw",
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.content == b"2345"
