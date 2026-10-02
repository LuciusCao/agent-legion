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

import pytest

from server.app.services.external_artifact_access import ExternalArtifactAccessService
from server.app.services.job_artifact_media import attachment_disposition
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
    """Workspace + job WITH the intake-frozen definition snapshot (the shape
    production intake creates): the demo legacy-intake revision is published
    first so create_jobs_bulk freezes its JSON onto the job row — the
    declared-outputs gate (#703 codex round 4 P2-1) reads that snapshot."""
    from tests.helpers import publish_legacy_intake_revision

    job_db.create_workspace(workspace_id, default_workflow_key=workspace_id)
    revision = publish_legacy_intake_revision(job_db, workspace_id)
    batch = job_db.create_run(
        workspace_id,
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace_id,
    )
    job_ids = job_db.create_jobs_bulk(
        candidates=[{"entity_id": "Q1", "entity_type": "question", "title": "Question 1"}],
        workflow_key=workspace_id,
        run_id=batch["id"],
        node_keys=["question_understanding"],
        workspace_id=workspace_id,
        revision=revision,
    )
    return job_db.get_job(job_ids[0])


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


def _seed_bare_key_row(store, job, name: str, payload: bytes) -> str:
    """Register an UNCOMPRESSED manifest row (legacy/older-Worker form): the
    storage key carries no .gz suffix — the only form presigned for #739."""
    storage_key = f"jobs/{job['workspace_id']}/{job['id']}/{name}"
    store.storage.objects[storage_key] = payload
    store.record_remote(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key="upstream",
        name=name,
        storage_key=storage_key,
        size_bytes=len(payload),
        content_hash=hashlib.sha256(payload).hexdigest(),
    )
    return storage_key


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
    # 声明名（demo 快照的 outputs）：#703 codex4 后未声明名不再列出。
    (storage / "script.md").write_text("old", encoding="utf-8")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    entries = {e["name"]: e for e in listing["artifacts"]}
    assert entries["report.json"]["storage"] == "object"
    assert entries["script.md"]["storage"] == "local"
    assert entries["script.md"]["size_bytes"] is None
    assert entries["script.md"]["uploaded_at"] is None


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
    (storage / "script.md").write_text("{}", encoding="utf-8")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    assert listing["object_storage_enabled"] is False
    assert [e["name"] for e in listing["artifacts"]] == ["script.md"]
    assert listing["artifacts"][0]["storage"] == "local"


def test_status_artifact_names_union(job_db, settings):
    """status 的名字清单 = 本地名 ∪ manifest 名（enabled 时）。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_manifest_row(store, job, "report.json", b"{}")
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("old", encoding="utf-8")

    payload = service.status(job["workspace_id"], job["id"])

    assert payload["artifacts"] == ["report.json", "script.md"]


# --- P2-1: 子路径名 -----------------------------------------------------------


def _seed_job_with_subpath_output(job_db, workspace_id: str = "ws-a") -> dict:
    """Demo variant whose publish node declares the nested output
    ``reports/final.json`` — the declared-subpath shape #631 P2-1 serves."""
    import dataclasses

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_demo_legacy_intake_definition

    job_db.create_workspace(workspace_id, default_workflow_key=workspace_id)
    definition = load_demo_legacy_intake_definition()
    nodes = dict(definition.nodes)
    nodes["publish_content"] = dataclasses.replace(
        nodes["publish_content"], outputs=["reports/final.json"]
    )
    definition = dataclasses.replace(definition, key=workspace_id, nodes=nodes)
    revision = WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)
    batch = job_db.create_run(
        workspace_id, "batch_by_ids", {"question_ids": ["Q1"]}, workspace_id=workspace_id
    )
    job_ids = job_db.create_jobs_bulk(
        candidates=[{"entity_id": "Q1", "entity_type": "question", "title": "Q1"}],
        workflow_key=workspace_id,
        run_id=batch["id"],
        node_keys=["question_understanding"],
        workspace_id=workspace_id,
        revision=revision,
    )
    return job_db.get_job(job_ids[0])


def test_list_artifacts_includes_local_subpath_names(job_db, settings):
    """#631 review P2-1: local-only 子路径产物（reports/final.json——声明
    outputs 里的嵌套名）必须被深度扫描列出（根级扫描漏掉子目录文件），
    名字与 raw 端点可下载名一致。"""
    job = _seed_job_with_subpath_output(job_db)
    service = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    storage = resolve_job_dir(job, job_db.jobs_dir)
    (storage / "reports").mkdir(parents=True, exist_ok=True)
    (storage / "reports" / "final.json").write_text("{}", encoding="utf-8")
    (storage / "script.md").write_text("top", encoding="utf-8")

    payload = service.list_artifacts(job["workspace_id"], job["id"])

    assert [e["name"] for e in payload["artifacts"]] == [
        "reports/final.json",
        "script.md",
    ]


def test_status_lists_local_subpath_names(job_db, settings):
    job = _seed_job_with_subpath_output(job_db)
    service = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    storage = resolve_job_dir(job, job_db.jobs_dir)
    (storage / "reports").mkdir(parents=True, exist_ok=True)
    (storage / "reports" / "final.json").write_text("{}", encoding="utf-8")

    payload = service.status(job["workspace_id"], job["id"])

    assert payload["artifacts"] == ["reports/final.json"]


def _seed_job_with_deep_output(job_db, workspace_id: str = "ws-a") -> dict:
    """Variant declaring a multi-level output ``reports/2026/final.json`` —
    the walk-pruning regression probe (#703 codex round 4 P2-1)：中途目录
    不是声明名本身，剪枝判据必须按前缀链下探，不能只认首段。"""
    import dataclasses

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_demo_legacy_intake_definition

    job_db.create_workspace(workspace_id, default_workflow_key=workspace_id)
    definition = load_demo_legacy_intake_definition()
    nodes = dict(definition.nodes)
    nodes["publish_content"] = dataclasses.replace(
        nodes["publish_content"], outputs=["reports/2026/final.json"]
    )
    definition = dataclasses.replace(definition, key=workspace_id, nodes=nodes)
    revision = WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)
    batch = job_db.create_run(
        workspace_id, "batch_by_ids", {"question_ids": ["Q1"]}, workspace_id=workspace_id
    )
    job_ids = job_db.create_jobs_bulk(
        candidates=[{"entity_id": "Q1", "entity_type": "question", "title": "Q1"}],
        workflow_key=workspace_id,
        run_id=batch["id"],
        node_keys=["question_understanding"],
        workspace_id=workspace_id,
        revision=revision,
    )
    return job_db.get_job(job_ids[0])


def test_multi_level_declared_output_is_walked_and_downloadable(job_db, settings):
    """多级声明 outputs（reports/2026/final.json）照常列举与下载：walk 中
    途目录（reports/2026）不在声明名集合里，但它是声明名的目录前缀——
    剪枝按前缀链下探（首段判据会把它剪掉，深度声明产物平白消失）。"""
    from server.app.services.job_artifacts import JobArtifactService

    job = _seed_job_with_deep_output(job_db)
    service = ExternalArtifactAccessService(
        job_db, settings, artifact_service=JobArtifactService(job_db, None)
    )
    storage = resolve_job_dir(job, job_db.jobs_dir)
    (storage / "reports" / "2026").mkdir(parents=True, exist_ok=True)
    (storage / "reports" / "2026" / "final.json").write_text('{"deep": 1}', encoding="utf-8")
    (storage / "reports" / "notes.txt").write_text("undeclared sibling", encoding="utf-8")

    payload = service.list_artifacts(job["workspace_id"], job["id"])

    assert [e["name"] for e in payload["artifacts"]] == ["reports/2026/final.json"]

    raw = service.open_raw_current(job["workspace_id"], job["id"], "reports/2026/final.json")
    assert raw.path is not None
    assert raw.path.read_bytes() == b'{"deep": 1}'

    # 未声明邻居名：下载门 404（NotFoundError）。
    with pytest.raises(NotFoundError):
        service.open_raw_current(job["workspace_id"], job["id"], "reports/notes.txt")


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


# --- #739: presigned download_url -------------------------------------------------


def test_list_artifacts_presigns_bare_key_rows(job_db, settings):
    """#739：非 .gz 对象行签发 presigned GET——URL 只针对行的 storage_key
    （服务端布局生成的 key，无请求输入参与），expires_at = now + 实例 TTL。"""
    from datetime import UTC, datetime, timedelta

    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    key = _seed_bare_key_row(store, job, "clip.mp4", b"0123456789")

    before = datetime.now(UTC)
    listing = service.list_artifacts(job["workspace_id"], job["id"])
    after = datetime.now(UTC)

    entry = next(e for e in listing["artifacts"] if e["name"] == "clip.mp4")
    assert entry["storage"] == "object"
    # FakeObjectStorage 的 presign_get 返回 key 派生 URL 并记录调用——签名
    # 对象就是行的 storage_key，不是 job_id/name 拼接（防 key 注入面）。
    assert store.storage.presigned_gets == [key]
    assert entry["download_url"] == f"https://s3.test/download/{key}"
    assert entry["content_encoding"] == ""
    # TTL 断言：默认 3600（实例设置契约的默认值）。
    assert store.storage.get_expiries == [3600]
    assert (
        before + timedelta(seconds=3600) <= entry["expires_at"] <= after + timedelta(seconds=3600)
    )


def test_list_artifacts_respects_instance_presign_ttl(job_db, settings):
    """实例设置改 TTL（重启生效语义：测试直接改运行时块），签发秒数跟随。"""
    settings.executor_runtime.agent_workers.artifact_download_presign_ttl_seconds = 600

    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_bare_key_row(store, job, "clip.mp4", b"0123456789")

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    assert store.storage.get_expiries == [600]
    entry = next(e for e in listing["artifacts"] if e["name"] == "clip.mp4")
    assert entry["expires_at"] is not None


def test_list_artifacts_gzip_rows_are_presigned_with_gzip_encoding(job_db, settings):
    """#338/#739 codex P2：v4+ Worker 的产物全是 .gz——排除它们等于直连
    对真实远程产物整体失效。.gz 行照常签发，Content-Encoding: gzip 作为
    S3 响应覆盖签进 URL（与 raw 端点透传同一表示），content_encoding 标
    存储态。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_manifest_row(store, job, "report.json", b'{"r": 1}')

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    key = str(next(iter(store.rows_for_job(job["id"])))["storage_key"])
    assert key.endswith(".gz")
    assert store.storage.presigned_gets == [key]
    entry = next(e for e in listing["artifacts"] if e["name"] == "report.json")
    assert entry["download_url"] == f"https://s3.test/download/{key}"
    assert entry["expires_at"] is not None
    assert entry["content_encoding"] == "gzip"
    assert store.storage.get_response_headers == [
        {
            "ResponseContentType": "application/octet-stream",
            "ResponseContentDisposition": attachment_disposition("report.json"),
            "ResponseContentEncoding": "gzip",
        }
    ]


@pytest.mark.parametrize(
    ("name", "gzipped"),
    [("clip.mp4", False), ("clip.mp4", True), ("report.pdf", False), ('a "b".json', False)],
)
def test_presigned_headers_match_raw_endpoint_policy(name, gzipped):
    """#739 codex P2（媒体类型）：直连的响应头与 raw_response 同源——
    Content-Type 走同一白名单、非白名单同样 attachment（含 RFC 6266 转义）、
    gzip 同样附 Content-Encoding。逐头对照 raw 端点实际构造的响应。"""
    import io

    from server.app.routes.job_artifact_raw_response import raw_response
    from server.app.services.external_artifact_access import _presigned_headers
    from server.app.services.job_artifact_raw_types import RawArtifact

    raw = raw_response(
        RawArtifact(
            name=name,
            stream=io.BytesIO(b"x"),
            size_bytes=1,
            content_encoding="gzip" if gzipped else None,
        )
    )
    expected = {"ResponseContentType": raw.media_type}
    if "content-disposition" in raw.headers:
        expected["ResponseContentDisposition"] = raw.headers["content-disposition"]
    if gzipped:
        expected["ResponseContentEncoding"] = raw.headers["content-encoding"]
    assert _presigned_headers(name, gzipped) == expected


def test_list_artifacts_local_and_disabled_entries_have_no_url(job_db, settings):
    """local 条目与未配置对象存储时：download_url/expires_at 全空（对象存储
    之外没有可签发的东西），object_storage_enabled 语义不变。
    ``script.md`` 是 job 快照的声明产物名——local 清单收窄到声明名
    （#703 codex round 4 P2-1）。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_bare_key_row(store, job, "clip.mp4", b"0123456789")
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "script.md").write_text("old", encoding="utf-8")

    listing = service.list_artifacts(job["workspace_id"], job["id"])
    entries = {e["name"]: e for e in listing["artifacts"]}

    assert entries["script.md"]["storage"] == "local"
    assert entries["script.md"]["download_url"] is None
    assert entries["script.md"]["expires_at"] is None
    assert entries["script.md"]["content_encoding"] == ""
    assert listing["object_storage_enabled"] is True

    # 未配置 bucket（store disabled）：对象行本来就不会列出，local 行无 URL。
    disabled = ExternalArtifactAccessService(job_db, settings, object_store=_NoStorage())
    degraded = disabled.list_artifacts(job["workspace_id"], job["id"])
    assert degraded["object_storage_enabled"] is False
    for entry in degraded["artifacts"]:
        assert entry["download_url"] is None
        assert entry["expires_at"] is None


def test_status_poll_is_unsigned_and_survives_signing_failure(job_db, settings, monkeypatch):
    """#739 codex P2：status 走免签名名称管线——轮询 N 次零 presign 调用
    （计数桩断言 0），签名客户端/凭据异常不传染状态面（仍返回完整名单，
    状态面只依赖 DB）；对照组证明桩是活的、清单面确实签名。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    _seed_bare_key_row(store, job, "clip.mp4", b"0123456789")
    _seed_manifest_row(store, job, "report.json", b'{"r": 1}')
    calls: list[str] = []

    def _boom(storage_key, expires_seconds=3600, response_headers=None):  # noqa: ANN001, ANN202
        calls.append(storage_key)
        raise ConnectionError("signing credentials broken")

    monkeypatch.setattr(store.storage, "presign_get", _boom)

    for _ in range(3):
        payload = service.status(job["workspace_id"], job["id"])
        assert payload["artifacts"] == ["clip.mp4", "report.json"]

    assert calls == []

    # 对照：同一必炸桩下清单接口仍走到 presign（桩活、签名面未退化）。
    with pytest.raises(ConnectionError, match="signing credentials broken"):
        service.list_artifacts(job["workspace_id"], job["id"])
    assert calls


def test_presign_targets_exactly_the_manifest_row_key(job_db, settings):
    """安全面（签名目标）：presign_get 收到的必须逐位等于权威 manifest 行
    的 storage_key——行由服务端布局生成（record_remote/verify_remote 拒绝
    布局外 key），这是「请求输入无法影响签名目标」的落点。测试从 DB 读回
    行断言（不自己拼 key），并混入一条 gzip 行钉住逐行对应：签错对象
    （拼名、漏 workspace 段、签成别行的 key、跨条目乱签）都会红。"""
    job = _seed_job(job_db)
    store = JobArtifactObjectStore(job_db, FakeObjectStorage())
    service = ExternalArtifactAccessService(job_db, settings, object_store=store)
    bare_key = _seed_bare_key_row(store, job, "clip.mp4", b"0123456789")
    _seed_manifest_row(store, job, "report.json", b'{"r": 1}')  # .gz 孪生条目

    rows = {str(row["name"]): row for row in store.rows_for_job(job["id"])}
    assert str(rows["clip.mp4"]["storage_key"]) == bare_key  # 行与种子一致（自检）
    settings.executor_runtime.agent_workers.artifact_download_presign_ttl_seconds = 7200

    listing = service.list_artifacts(job["workspace_id"], job["id"])

    entry = next(e for e in listing["artifacts"] if e["name"] == "clip.mp4")
    # 签发名单：每行恰好一次、恰好该行的 key。
    assert sorted(store.storage.presigned_gets) == sorted(
        str(row["storage_key"]) for row in rows.values()
    )
    # TTL 参数逐位等于实例配置值（防回落硬编码默认）。
    assert store.storage.get_expiries == [7200, 7200]
    # 响应字段就是那次调用的返回值（Fake 按 key 派生 URL——签错对象时两者
    # 同时偏离，双保险）。
    assert entry["download_url"] == f"https://s3.test/download/{bare_key}"
