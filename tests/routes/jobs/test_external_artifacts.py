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
        (storage / "script.md").write_text("{}", encoding="utf-8")

        response = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts")

    assert response.status_code == 200
    body = response.json()
    assert body["object_storage_enabled"] is False
    entry = next(e for e in body["artifacts"] if e["name"] == "script.md")
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
    # 声明名（demo 快照 outputs）——#703 codex4 后未声明名不可下载。
    (storage / "script.md").write_text("# script", encoding="utf-8")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/script.md/raw")

    assert response.status_code == 200
    # 白名单外扩展名（含 .json/.html）按 raw 白名单策略强制下载。
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.content == b"# script"


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
    """Path-traversal immunity with the ``{artifact_name:path}`` converter
    (#631 review P2-1): the converter deliberately captures subpath names
    (``reports/final.json``), so the guard moved into the service — an
    absolute name, ``..`` segment or backslash is a 400, never a file read
    outside the job_dir (the bare ``/jobs/{job_id}/artifacts/{name:path}``
    route rejects the same family in the same place)."""
    c, job_a, _ = two_workspaces

    for name in ("..%2Fagent_legion.sqlite", "%2e%2e%2Fagent_legion.sqlite"):
        response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{name}/raw")
        assert response.status_code == 400


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


# --- P1: scoped-token workspace 绑定 -----------------------------------------


def test_scoped_token_bound_to_other_workspace_is_404_on_all_three(two_workspaces, job_db):
    """#631 review P1: a Bearer token bound to scoped_workspace_id=ws-a must
    not read through ws-b even though the minting admin can see every
    workspace (require_workspace_access checks the user, not the binding).
    Mismatches are 404, not 403 — this surface answers cross-workspace probes
    with not-found, keeping the no-enumeration semantics of the job check."""
    from server.app.auth import scoped_tokens

    c, job_a, job_b = two_workspaces
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id="ws-a")
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")

    # All three endpoints refuse the ws-b prefix for the ws-a-bound token.
    assert scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}").status_code == 404
    assert scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts").status_code == 404
    assert (
        scoped.get(f"/api/workspaces/ws-b/jobs/{job_b['id']}/artifacts/frame.png/raw").status_code
        == 404
    )
    # The bound workspace itself still reads normally (guard is a mismatch
    # check, not a scoped-token ban).
    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


def test_unbound_scoped_token_still_reads_member_workspaces(two_workspaces, job_db):
    """Unbound scoped tokens keep the membership-only behaviour (schema v45):
    no scoped_workspace_id → nothing to compare, the parent membership guard
    decides."""
    from server.app.auth import scoped_tokens

    c, job_a, _ = two_workspaces
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


# --- P2-1: 子路径产物名列出 + 下载往返 ---------------------------------------


def test_subpath_artifact_roundtrip_object_backed(two_workspaces):
    """#631 review P2-1: a declared output like ``reports/final.json`` keeps
    its subdirectory through the Worker channel (unpack, promote, manifest
    key) — the manifest lists it and the raw endpoint must be able to serve
    exactly that name."""
    c, job_a, _ = two_workspaces
    payload = b'{"final": true}'
    _register_object_artifact(c, job_a, "reports/final.json", payload)

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    entries = {e["name"]: e for e in listing["artifacts"]}
    assert "reports/final.json" in entries
    assert entries["reports/final.json"]["storage"] == "object"

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/reports/final.json/raw")
    assert response.status_code == 200
    assert response.content == payload

    # status 端点的名单也含子路径名（manifest 名并入）。
    status = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()
    assert "reports/final.json" in status["artifacts"]


def _publish_subpath_revision(c, workspace_id: str) -> None:
    """Publish the demo variant whose publish node declares the nested output
    ``reports/final.json`` (the declared-subpath shape), then create a job —
    its intake-frozen snapshot carries the nested declaration."""
    import dataclasses

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_demo_legacy_intake_definition

    definition = load_demo_legacy_intake_definition()
    nodes = dict(definition.nodes)
    nodes["publish_content"] = dataclasses.replace(
        nodes["publish_content"], outputs=["reports/final.json"]
    )
    definition = dataclasses.replace(definition, key=workspace_id, nodes=nodes)
    WorkflowRevisionService(c.app.state.job_db).publish_workspace_revision(workspace_id, definition)


def test_subpath_artifact_local_only_roundtrip(client_factory, monkeypatch):
    """Local-only subpath artifacts (instance without a bucket): the deep
    listing finds DECLARED files under subdirectories (the root-only scan
    missed them) and the raw endpoint serves them from the local copy."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        _publish_subpath_revision(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "reports").mkdir(parents=True, exist_ok=True)
        (storage / "reports" / "final.json").write_text('{"ok": 1}', encoding="utf-8")
        (storage / "script.md").write_text("top", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        entries = {e["name"]: e for e in listing["artifacts"]}
        assert entries["reports/final.json"]["storage"] == "local"
        assert entries["script.md"]["storage"] == "local"

        response = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/reports/final.json/raw")
        assert response.status_code == 200
        assert response.content == b'{"ok": 1}'

        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert "reports/final.json" in status["artifacts"]


def test_local_listing_prunes_runs_and_hidden_dirs(client_factory, monkeypatch):
    """The deep scan lists artifacts, not job_dir internals: ``runs/`` holds
    per-node run dirs (events.jsonl) and dot-directories are staging/trash —
    neither may surface as a downloadable artifact name."""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
        (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text("{}", encoding="utf-8")
        (storage / ".result-staging-x").mkdir(parents=True, exist_ok=True)
        (storage / ".result-staging-x" / "leak.txt").write_text("x", encoding="utf-8")
        (storage / "script.md").write_text("{}", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()

        names = [e["name"] for e in listing["artifacts"]]
        assert names == ["script.md"]


def test_list_and_status_only_advertise_downloadable_names(client_factory, monkeypatch, job_db):
    """#631 codex round 3 (P2-1)：清单与下载白名单对称。工作流可声明并生
    成下载侧必拒的名字（点前缀、``runs`` 段、超长段）——本地文件与对象
    manifest 行都不得把它们当产物列出（list 与 status 两个列举面同门），
    否则按清单调 raw 端点是 400，破坏 list→download 契约。声明期校验另
    立 issue，本用例只钉列举侧对齐。"""
    from server.app.db.transaction import write_transaction

    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", FakeObjectStorage())
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        # 本地落盘的必拒名：点前缀、runs 段、超长段（>200 字节）。
        (storage / ".report.json").write_text("{}", encoding="utf-8")
        (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
        (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text("{}", encoding="utf-8")
        (storage / ("x" * 201 + ".json")).write_text("{}", encoding="utf-8")
        (storage / "script.md").write_text("{}", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        # 对象 manifest 行同样过滤：绕过 Worker 校验直插一行点前缀名
        # （模拟未来写入方失守/行被污染），清单不列、按名 raw 仍 400。
        store: JobArtifactObjectStore = c.app.state.job_artifact_objects
        storage_key = f"jobs/ws-a/{job['id']}/.hidden.json"
        store.storage.objects[storage_key] = b"{}"
        with write_transaction(job_db) as conn:
            conn.execute(
                "insert into job_artifacts(job_id, node_key, name, storage_key,"
                " size_bytes, content_hash) values (%s, 'upstream', '.hidden.json',"
                " %s, 2, '')",
                (job["id"], storage_key),
            )

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        names = [e["name"] for e in listing["artifacts"]]
        assert ".hidden.json" not in names
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert ".hidden.json" not in status["artifacts"]
        assert (
            c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/.hidden.json/raw").status_code
            == 400
        )


# --- #703 codex round 4 (P2-1)：本地子路径面收窄到声明 outputs ----------------


def test_undeclared_nested_files_not_listed_not_downloadable(client_factory, monkeypatch):
    """code 节点在 job_dir 留下的未声明嵌套文件（scratch/debug.json）：修
    复前递归扫描把它当 local 产物公布、raw 端点按相对路径返回字节——中间
    文件被外部读取面暴露，且每次状态轮询都遍历非产物文件。现在列举与下
    载都收窄到 job 快照声明的 outputs（本用例的 workspace 只声明根级名，
    scratch/ 整棵子树既不列也不可达）。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        (storage / "scratch").mkdir(parents=True, exist_ok=True)
        (storage / "scratch" / "debug.json").write_text('{"internal": "debug"}', encoding="utf-8")
        (storage / "notes.txt").write_text("undeclared top-level", encoding="utf-8")
        (storage / "script.md").write_text("# declared", encoding="utf-8")

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        scratch_read = c.get(
            f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/scratch/debug.json/raw"
        )
        notes_read = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/notes.txt/raw")
        assert scratch_read.status_code == 404
        assert b"debug" not in scratch_read.content
        assert notes_read.status_code == 404
        # 声明名照常（对照，判别力：门收窄不误伤合法产物）。
        declared = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/script.md/raw")
        assert declared.status_code == 200
        assert declared.content == b"# declared"


def test_undeclared_download_404_but_manifest_row_still_serves(two_workspaces):
    """声明门只约束本地扫描面：对象 manifest 行是执行产物的登记面，不受
    「名字 ∈ 声明 outputs」约束——行在（Worker 登记的产物），即使快照声
    明里没有这个名字（例如清单行由未来写入方/跨版本升级登记），raw 照常
    读对象副本。"""
    c, job_a, _ = two_workspaces
    payload = b'{"registered": true}'
    _register_object_artifact(c, job_a, "undeclared/registered.json", payload)

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    names = [e["name"] for e in listing["artifacts"]]
    assert "undeclared/registered.json" in names

    response = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/undeclared/registered.json/raw"
    )
    assert response.status_code == 200
    assert response.content == payload


def test_no_snapshot_job_local_listing_empty_and_undeclared_404(
    client_factory, monkeypatch, job_db
):
    """无快照的 legacy job（snapshot 解析为 None → 声明集为空）：本地清单
    为空、未声明名下载 404——列举与下载同语义；manifest 行照常（enabled
    门控不变）。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "legacy.txt").write_text("old", encoding="utf-8")
        # 抹掉快照：模拟 v50 之前的 legacy 行。
        from server.app.db.transaction import write_transaction

        with write_transaction(job_db) as conn:
            conn.execute(
                "update jobs set workflow_definition_snapshot_json='' where id=%s",
                (job["id"],),
            )

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == []
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == []
        assert (
            c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/legacy.txt/raw").status_code
            == 404
        )


# --- #703 复审 MEDIUM-1/MEDIUM-2：列举与下载的两种新不对称 -------------------


def test_symlink_files_are_not_advertised(client_factory, monkeypatch, tmp_path):
    """#703 复审 MEDIUM-1：job_dir 内的 symlink 文件不进清单。
    ``os.walk(followlinks=False)`` 只剪枝目录链接；链接文件会以合法名过
    白名单进 list/status，而 raw 端包含性校验对指向 job_dir 外的目标答
    400——清单列了但 raw 拒绝，破坏 list→download 契约。链接整体跳过
    （与目录链接同语义），正常文件不受影响。"""
    with client_factory(fresh=True) as c:
        monkeypatch.setattr(c.app.state.job_artifact_objects, "storage", None)
        _seed_workspace(c, "ws-a")
        job = _create_job(c, "ws-a")
        storage = Path(job["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "script.md").write_text("{}", encoding="utf-8")
        outside = tmp_path / "outside.json"
        outside.write_text("outside-secret", encoding="utf-8")
        (storage / "link.json").symlink_to(outside)

        listing = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts").json()
        assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
        status = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}").json()
        assert status["artifacts"] == ["script.md"]

        # raw 端语义：链接名要么被声明门拦（404——名字未声明），要么到包
        # 含性校验被拒（400）；无论哪条路，都绝不读到 job_dir 外的目标字节。
        raw = c.get(f"/api/workspaces/ws-a/jobs/{job['id']}/artifacts/link.json/raw")
        assert raw.status_code in (400, 404)
        assert b"outside-secret" not in raw.content


def test_prefix_refused_manifest_rows_are_not_advertised(two_workspaces, job_db):
    """#703 复审 MEDIUM-2：名字过白名单但 storage_key 越界的 manifest 行。
    raw 端 H1 兜底对越界行必 404（manifest-first 命中行就短路，本地副本
    不再兜底），所以 list/status 不得以任何形态（object 条目或本地条目）
    列出该名字——同名本地文件也不回填，否则清单照列、raw 照 404。"""
    from server.app.db.transaction import write_transaction

    c, job_a, job_b = two_workspaces
    # 同名本地文件：没有它只是「行被过滤」；有它，旧的本地回填会把名字
    # 以 storage=local 复活，同样破坏契约。
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "evil.json").write_text('{"local": true}', encoding="utf-8")
    key = f"jobs/ws-b/{job_b['id']}/evil.json"  # storage_key 指向别的 job
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    store.storage.objects[key] = b"{}"
    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'upstream', 'evil.json', %s, 2, '')",
            (job_a["id"], key),
        )

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    assert [e["name"] for e in listing["artifacts"]] == []
    status = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()
    assert status["artifacts"] == []
    # raw 语义不变（H1 兜底）：越界行 404，本地副本不接管。
    assert (
        c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/evil.json/raw").status_code == 404
    )

    # 最新行判定（lookup 同语义）：同名行先合法后越界（显式更晚的
    # uploaded_at），raw 端 lookup 取最新（越界）行必 404——清单也不得
    # 拿旧行复活该名字。
    _register_object_artifact(c, job_a, "stale.json", b'{"v": 1}')
    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash, uploaded_at)"
            " values (%s, 'evil_writer', 'stale.json', %s, 2, '', now() + interval '1 hour')",
            (job_a["id"], f"jobs/ws-b/{job_b['id']}/stale.json"),
        )

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    assert "stale.json" not in [e["name"] for e in listing["artifacts"]]
    status = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").json()
    assert "stale.json" not in status["artifacts"]
    assert (
        c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/stale.json/raw").status_code
        == 404
    )


# --- #631 攻击复审 M1/M2：下载侧名字白名单（与清单剪枝单一事实来源） --------


def test_raw_serves_runs_dir_file_only_via_manifest_row(two_workspaces):
    """M2 不对称收口：``runs/`` 是执行内部数据（events.jsonl），清单剪掉
    它；下载侧此前只做包含性校验、不认识 runs/——文件一旦落盘即可按名
    下载（清单不列但可达）。现在 ``_artifact_path`` 拒绝 runs/ 前缀段与
    点前缀段，与 ``artifact_names_deep`` 的剪枝规则同一份名单。"""
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    (storage / "runs" / "node_a" / "token1").mkdir(parents=True, exist_ok=True)
    (storage / "runs" / "node_a" / "token1" / "events.jsonl").write_text(
        '{"internal": "run-events"}', encoding="utf-8"
    )
    (storage / ".result-staging-x").mkdir(parents=True, exist_ok=True)
    (storage / ".result-staging-x" / "leak.txt").write_text("staged", encoding="utf-8")

    runs_read = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/runs/node_a/token1/events.jsonl/raw"
    )
    dot_read = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/.result-staging-x/leak.txt/raw"
    )

    assert runs_read.status_code == 400
    assert dot_read.status_code == 400


@pytest.mark.parametrize(
    "name",
    [
        "result.json%00",  # NUL：decode 后是控制字符
        "x" * 300,  # 超长段：ENAMETOOLONG 家族
        "y" * 201,  # 段上限（200 字节）刚过线
    ],
)
def test_raw_rejects_control_char_and_oversized_names(two_workspaces, name):
    """M1：畸形名字必须是 400，不是让 lstat/stat 炸 500（no-enumeration
    语义：500 vs 404 把「名字是否畸形」变成侧信道）。"""
    c, job_a, _ = two_workspaces
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("{}", encoding="utf-8")

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{name}/raw")

    assert response.status_code == 400


def test_raw_null_byte_name_is_400_not_500(two_workspaces):
    """M1：``%00`` 解码后进 lstat 是 ValueError（embedded null）——修复前
    逃出端点成 500（TestClient 直接 raise），修复后名字白名单先拒。"""
    c, job_a, _ = two_workspaces

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/result.json%00/raw")

    assert response.status_code == 400


def test_status_null_byte_job_id_is_400_not_500(two_workspaces):
    """M1：``%00`` 进 job_id 修复前让 psycopg 抛 DataError（500）；现在
    路由层早拒 400（status / artifacts / raw 三端点同门）。"""
    c, _, _ = two_workspaces

    status = c.get("/api/workspaces/ws-a/jobs/%00foo")
    listing = c.get("/api/workspaces/ws-a/jobs/%00foo/artifacts")
    raw = c.get("/api/workspaces/ws-a/jobs/%00foo/artifacts/x.json/raw")

    assert status.status_code == 400
    assert listing.status_code == 400
    assert raw.status_code == 400


def test_deep_subpath_and_bare_names_still_serve(two_workspaces):
    """白名单不误伤：合法子路径产物（深目录）、普通名照常 200——名字深度
    与声明门无关（深嵌套声明 outputs 照常服务），这里走 manifest 行（行
    不受声明门约束）。"""
    c, job_a, _ = two_workspaces
    deep_name = "/".join(f"level{i}" for i in range(8)) + "/final.json"
    payload = b"{}"
    _register_object_artifact(c, job_a, deep_name, payload)
    _register_object_artifact(c, job_a, "top.png", b"\x89PNG")

    deep_read = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/{deep_name}/raw")
    top_read = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/top.png/raw")

    assert deep_read.status_code == 200
    assert deep_read.content == b"{}"
    assert top_read.status_code == 200
    assert top_read.content == b"\x89PNG"


# --- #631 攻击复审 H1：读侧 storage_key 前缀兜底 ------------------------------


def test_raw_refuses_manifest_row_pointing_outside_job_prefix(two_workspaces, job_db):
    """H1：manifest 行是对象读路径的唯一权威（表上没有 workspace 列）。
    直接 SQL 把 ws-a job 的行指向 ws-b 的对象 key（模拟行被污染/未来
    写入方失守）——读侧兜底必须 404，绝不读穿 workspace 边界。"""
    import gzip
    import hashlib

    from server.app.db.transaction import write_transaction

    c, job_a, job_b = two_workspaces
    secret_b = b"WSB-SECRET-FRAME-BYTES"
    stored = gzip.compress(secret_b)
    key_b = f"jobs/ws-b/{job_b['id']}/frame.png.gz"
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    store.storage.objects[key_b] = stored

    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'upstream', 'leak.png', %s, %s, %s)",
            (
                job_a["id"],
                key_b,
                len(stored),
                hashlib.sha256(secret_b).hexdigest(),
            ),
        )

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/leak.png/raw")

    assert response.status_code == 404


def test_text_read_refuses_manifest_row_pointing_outside_job_prefix(two_workspaces, job_db):
    """H1 文本分支：``/artifacts/{name}`` 的 legacy 读路径同样兜底（修复
    前清单照常列出、文本读照常返回跨 workspace 字节）。"""
    import gzip
    import hashlib

    from server.app.db.transaction import write_transaction

    c, job_a, job_b = two_workspaces
    secret_b = b"WSB-SECRET-NOTES"
    stored = gzip.compress(secret_b)
    key_b = f"jobs/ws-b/{job_b['id']}/notes.json.gz"
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    store.storage.objects[key_b] = stored

    with write_transaction(job_db) as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key,"
            " size_bytes, content_hash) values (%s, 'upstream', 'notes.json', %s, %s, %s)",
            (job_a["id"], key_b, len(stored), hashlib.sha256(secret_b).hexdigest()),
        )

    response = c.get(f"/api/jobs/{job_a['id']}/artifacts/notes.json")

    assert response.status_code == 404


def test_raw_serves_rows_within_job_prefix_after_guard(two_workspaces):
    """兜底不误伤：本 job 前缀内的行（.gz 与裸 key 两种形态）照常读。"""
    c, job_a, _ = two_workspaces
    payload = b"\x89PNG-current"
    _register_object_artifact(c, job_a, "frame.png", payload)
    _register_object_artifact(c, job_a, "clip.mp4", b"0123456789", gzipped=False)

    gz_read = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/frame.png/raw")
    bare_read = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/clip.mp4/raw")

    assert gz_read.status_code == 200 and gz_read.content == payload
    assert bare_read.status_code == 200 and bare_read.content == b"0123456789"


# --- #631 攻击复审 H2：legacy 裸路由的 scoped 语义（#745 rebase 后） ----------


def test_bare_job_read_routes_scope_scoped_tokens_by_binding(two_workspaces, job_db):
    """H2 收口的 rebase 修正（#703 CI 失败）：裸路由（无 workspace 前缀）
    修复前对任意 scoped Bearer token 全开——绑定 ws-a 的 token 可读 ws-b
    的 job 详情、清单、raw 字节、日志与 token 用量，整体绕过 #631 的
    workspace 隔离。#631 曾以路由级 scoped-一律-404 收口；rebase #745 后
    job_group 的 require_job_workspace_access 按 job 行反查授权域，裸路
    由与前缀家族同一语义——绑定 token 读自己 workspace 的裸路由保持
    200（#745 IDOR 矩阵钉住的既有行为），跨 workspace 与未知 job 同为
    404（防枚举常量信号，与 #631 的收口强度一致）。"""
    from server.app.auth import scoped_tokens

    c, job_a, job_b = two_workspaces
    _register_object_artifact(c, job_a, "frame-a.png", b"\x89PNG-a")
    _register_object_artifact(c, job_b, "frame.png", b"\x89PNG-bytes")
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id="ws-a")
    scoped = c.__class__(c.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    # 绑定 ws-a 的 token 走裸路由读 ws-b 的产物字节：拒绝（404）。
    assert scoped.get(f"/api/jobs/{job_b['id']}").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/artifacts/frame.png/raw").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/artifacts/frame.json").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/runs/1/log").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/token-usage").status_code == 404
    assert scoped.get(f"/api/jobs/{job_b['id']}/runs/1/token-usage").status_code == 404
    # 不存在的 job 同样 404：常量信号，无探测差异。
    assert scoped.get("/api/jobs/nope").status_code == 404

    # 绑定 workspace 自己的 job：裸路由照常可读（#745 的既有行为——
    # 守卫是归属校验，不是 scoped 一刀切）。
    assert scoped.get(f"/api/jobs/{job_a['id']}").status_code == 200

    # 全会话用户不受影响（前端控制台在用的面）。
    assert c.get(f"/api/jobs/{job_a['id']}").status_code == 200

    # scoped 身份的 sanctioned 读面照常：绑定 ws-a 读 ws-a 前缀端点 200。
    assert scoped.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}").status_code == 200


# --- P2-2: raw 优先权威 manifest 对象 -----------------------------------------


def test_raw_prefers_object_bytes_when_local_cache_stale(two_workspaces):
    """#631 review P2-2: the listing just published the manifest row's
    content_hash/uploaded_at as the current result — the download must serve
    the object those fields describe. A stale local cache (rerun replaced the
    file while the re-upload/row-upsert had not landed) must not win."""
    c, job_a, _ = two_workspaces
    current = b'{"execution": "current"}'
    _register_object_artifact(c, job_a, "report.json", current)
    # The stale cache: different bytes, same name (what a rerun left behind).
    storage = Path(job_a["storage_dir"])
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "report.json").write_text('{"execution": "stale-local"}', encoding="utf-8")

    # What the manifest advertises (the row's hash is over `current`).
    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    entry = next(e for e in listing["artifacts"] if e["name"] == "report.json")
    assert entry["storage"] == "object"

    response = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/report.json/raw")

    assert response.status_code == 200
    assert response.content == current  # the object bytes, not the local copy
    assert hashlib.sha256(response.content).hexdigest() == entry["content_hash"]
