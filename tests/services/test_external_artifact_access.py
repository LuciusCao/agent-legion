"""ExternalArtifactAccessService (#631): workspace binding + manifest listing.

Service-level contract: the workspace check doubles as the existence check
(cross-workspace 404, no id enumeration), the listing merges the authoritative
object-storage manifest with legacy local job_dir names, and the
execution-distinguishing metadata (content_hash / uploaded_at, #508) comes
from the CURRENT manifest row per name.
"""

from __future__ import annotations

import gzip
import hashlib

from server.app.services.external_artifact_access import ExternalArtifactAccessService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_errors import NotFoundError
from server.app.storage_paths import resolve_job_dir
from tests.fakes.storage import FakeObjectStorage


class _NoStorage:
    """Double for an instance without a bucket (None / disabled store)."""

    enabled = False

    def rows_for_job(self, job_id):  # noqa: ANN001, ANN202
        raise AssertionError("rows_for_job must not be called when disabled")

    def names_for_job(self, job_id):  # noqa: ANN001, ANN202
        raise AssertionError("names_for_job must not be called when disabled")


def _seed_job(job_db, workspace_id: str = "ws-a") -> dict:
    job_db.create_workspace(workspace_id, default_workflow_key="demo_workflow")
    batch = job_db.create_run(
        "demo_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace_id,
    )
    return job_db.create_job(
        workflow_key="demo_workflow",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding"],
        workspace_id=workspace_id,
    )


def _seed_manifest_row(
    store, job, name: str, payload: bytes, *, node_key: str = "upstream"
) -> None:
    stored = gzip.compress(payload)
    storage_key = f"jobs/{job['workspace_id']}/{job['id']}/{name}.gz"
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


# --- 归属校验 ----------------------------------------------------------------


def test_cross_workspace_job_is_404(job_db, settings):
    """跨 workspace 的 job_id 按不存在处理（404 语义），不能枚举。"""
    job = _seed_job(job_db, "ws-a")
    _seed_job(job_db, "ws-b")
    service = ExternalArtifactAccessService(job_db, settings)

    for method in (service.status, service.list_artifacts):
        try:
            method("ws-b", job["id"])
            raise AssertionError("expected NotFoundError")
        except NotFoundError as exc:
            assert "Job not found" in str(exc)


def test_unknown_job_is_404(job_db, settings):
    service = ExternalArtifactAccessService(job_db, settings)

    try:
        service.status("ws-a", "missing")
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


# --- 状态视图 ----------------------------------------------------------------


def test_status_lightweight_fields(job_db, settings):
    job = _seed_job(job_db)
    service = ExternalArtifactAccessService(job_db, settings)

    payload = service.status(job["workspace_id"], job["id"])

    assert payload["job_id"] == job["id"]
    assert payload["workspace_id"] == job["workspace_id"]
    assert payload["status"] == job["status"]
    assert payload["artifacts"] == []
    assert set(payload) == {
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


# --- 清单 --------------------------------------------------------------------


def test_list_artifacts_manifest_rows_with_metadata(job_db, settings):
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    payload = b'{"r": 1}'
    _seed_manifest_row(store, job, "report.json", payload)

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    assert listing["object_storage_enabled"] is True
    entry = next(e for e in listing["artifacts"] if e["name"] == "report.json")
    assert entry["storage"] == "object"
    assert entry["node_key"] == "upstream"
    assert entry["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert entry["uploaded_at"] is not None
    assert entry["media_type"] == "application/octet-stream"
    assert entry["size_bytes"] == len(gzip.compress(payload))  # stored size (#338)


def test_list_artifacts_merges_local_only_names(job_db, settings):
    """manifest 行（对象）与 legacy 本地名并存：一行一条，local 名无元数据。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_manifest_row(store, job, "report.json", b"{}")
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "legacy.txt").write_text("old", encoding="utf-8")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    entries = {e["name"]: e for e in listing["artifacts"]}
    assert entries["report.json"]["storage"] == "object"
    assert entries["legacy.txt"]["storage"] == "local"
    assert entries["legacy.txt"]["size_bytes"] is None
    assert entries["legacy.txt"]["uploaded_at"] is None


def test_list_artifacts_latest_row_per_name_after_rerun(job_db, settings):
    """#508：重跑后同 (node, name) 的行被 upsert——清单回答当前执行的副本。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_manifest_row(store, job, "report.json", b'{"v": 1}', node_key="node_a")
    _seed_manifest_row(store, job, "report.json", b'{"v": 2}', node_key="node_a")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    entries = [e for e in listing["artifacts"] if e["name"] == "report.json"]
    assert len(entries) == 1
    assert entries[0]["content_hash"] == hashlib.sha256(b'{"v": 2}').hexdigest()


def test_list_artifacts_disabled_store_lists_local_names(job_db, settings):
    """未配置 bucket：只有本地名（storage=local），不触对象存储读。"""
    job = _seed_job(job_db)
    service = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text("{}", encoding="utf-8")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    assert listing["object_storage_enabled"] is False
    assert [e["name"] for e in listing["artifacts"]] == ["result.json"]
    assert listing["artifacts"][0]["storage"] == "local"


def test_status_artifact_names_union(job_db, settings):
    """status 的名字清单 = 本地名 ∪ manifest 名（enabled 时）。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_manifest_row(store, job, "report.json", b"{}")
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "legacy.txt").write_text("old", encoding="utf-8")

    payload = service.status(job["workspace_id"], job["id"])

    assert payload["artifacts"] == ["legacy.txt", "report.json"]


# --- P2-1: 子路径名 -----------------------------------------------------------


def test_list_artifacts_includes_local_subpath_names(job_db, settings):
    """#631 review P2-1: local-only 子路径产物（reports/final.json）必须被
    深度扫描列出（根级扫描漏掉子目录文件），名字与 raw 端点可下载名一致。"""
    job = _seed_job(job_db)
    service = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    storage = resolve_job_dir(job, job_db.jobs_dir)
    (storage / "reports").mkdir(parents=True, exist_ok=True)
    (storage / "reports" / "final.json").write_text("{}", encoding="utf-8")
    (storage / "top.txt").write_text("top", encoding="utf-8")

    payload = service.list_artifacts(job["workspace_id"], job["id"])

    assert [e["name"] for e in payload["artifacts"]] == [
        "reports/final.json",
        "top.txt",
    ]


def test_status_lists_local_subpath_names(job_db, settings):
    job = _seed_job(job_db)
    service = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    storage = resolve_job_dir(job, job_db.jobs_dir)
    (storage / "reports").mkdir(parents=True, exist_ok=True)
    (storage / "reports" / "final.json").write_text("{}", encoding="utf-8")

    payload = service.status(job["workspace_id"], job["id"])

    assert payload["artifacts"] == ["reports/final.json"]


# --- P2-2: raw 优先权威 manifest 对象 -----------------------------------------


def test_open_raw_current_prefers_manifest_object_over_local(job_db):
    """#631 review P2-2: manifest 行存在时 raw 读对象副本（清单刚把行的
    content_hash 当当前结果公布），本地缓存可能滞后；无行才回落本地。"""
    from server.app.services.job_artifacts import JobArtifactService

    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    current = b'{"v": "current"}'
    _seed_manifest_row(store, job, "report.json", current)
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "report.json").write_bytes(b'{"v": "stale-local"}')
    service = JobArtifactService(job_db, store)

    raw = service.open_raw_current(job["id"], "report.json")

    assert raw.stream is not None
    # .gz 行按存储字节透传（#338，Content-Encoding 由路由层加）——解压后
    # 才是清单 content_hash 语义的内容字节。
    assert gzip.decompress(raw.stream.read()) == current
    assert raw.path is None

    # 对照：无 manifest 行的 local-only 产物仍从本地文件读。
    (storage / "legacy.txt").write_bytes(b"legacy")
    legacy = service.open_raw_current(job["id"], "legacy.txt")
    assert legacy.path is not None
    assert legacy.path.read_bytes() == b"legacy"
