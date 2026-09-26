"""External artifact-access routes (#631): the object-store branch of the
raw download surface (manifest-first reads, Range, subpath names, the H1
storage_key prefix guard).

Split from test_external_artifacts.py when it crossed the 800-line
test-file budget (#779 codex train review P1-3); cases migrated verbatim.
Shared seeding lives in external_artifact_testlib.py / the directory
conftest (``two_workspaces``).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.routes.jobs.external_artifact_testlib import _register_object_artifact


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


# --- #703 codex round 4 (P2-1)：声明门只约束本地扫描面 ------------------------


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


# --- #631 攻击复审 M2 白名单不误伤 --------------------------------------------


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


def test_raw_percent_encoded_reserved_chars_roundtrip(two_workspaces):
    """#779 列车 R3 复审（runbook 编码修复的常驻钉）：清单名里的 URL 保留
    字符（#/?）必须 percent-encode 后拼接（否则客户端按 fragment/query
    截断）；服务端对编码后的多段名解码后照常匹配 {artifact_name:path}
    并服务字节。"""
    c, job_a, _ = two_workspaces
    _register_object_artifact(c, job_a, "reports/summary#v2?.json", b'{"ok": 1}')

    listing = c.get(f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts").json()
    assert "reports/summary#v2?.json" in [e["name"] for e in listing["artifacts"]]

    response = c.get(
        f"/api/workspaces/ws-a/jobs/{job_a['id']}/artifacts/reports/summary%23v2%3F.json/raw"
    )

    assert response.status_code == 200
    assert response.content == b'{"ok": 1}'
