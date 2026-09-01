import gzip
import hashlib
import io
from pathlib import Path

import pytest

from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir
from tests.fakes.storage import FakeObjectStorage


@pytest.fixture
def artifact_service(job_db):
    return JobArtifactService(job_db)


@pytest.fixture
def job(job_db):
    workspace = job_db.create_workspace("default", default_workflow_key="demo_workflow")
    batch = job_db.create_run(
        "demo_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    return job_db.create_job(
        workflow_key="demo_workflow",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["question_understanding"],
        workspace_id=workspace["id"],
    )


def test_job_artifact_service_reads_file(artifact_service, job, job_db):
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")

    result = artifact_service.read(job["id"], "result.json")

    assert result["name"] == "result.json"
    assert result["content"] == '{"ok": true}'


def test_job_artifact_service_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.read(job["id"], "../agent_legion.sqlite")


def test_job_artifact_service_missing_job(artifact_service):
    with pytest.raises(NotFoundError, match="Job not found"):
        artifact_service.read("missing", "result.json")


def test_job_artifact_service_reject_subpath(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid job path"):
        artifact_service.reject_subpath(job["id"])


class _FakeObjectStore:
    """In-memory object-store double; ``error`` simulates a storage failure
    (e.g. NoSuchKey after a bucket lifecycle deletion)."""

    enabled = True

    def __init__(self, payload: bytes = b"", error: Exception | None = None):
        self._payload = payload
        self._error = error

    def lookup(self, job_id: str, name: str) -> dict:
        return {
            "storage_key": f"jobs/ws/{job_id}/{name}",
            "size_bytes": len(self._payload),
        }

    def open_stream(self, row: dict) -> io.BytesIO:
        if self._error is not None:
            raise self._error
        return io.BytesIO(self._payload)


def test_job_artifact_service_reads_from_object_store(job_db, job):
    """本地缓存已淘汰时从对象存储回读成功。"""
    service = JobArtifactService(job_db, _FakeObjectStore(payload=b'{"from": "store"}'))

    result = service.read(job["id"], "result.json")

    assert result == {"name": "result.json", "content": '{"from": "store"}'}


def test_job_artifact_service_object_error_becomes_404(job_db, job):
    """对象被 lifecycle 删除 / 存储故障 → 按未找到处理（404），不冒泡 500。"""
    from botocore.exceptions import ClientError

    boto_outage = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    service = JobArtifactService(job_db, _FakeObjectStore(error=boto_outage))

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.read(job["id"], "result.json")


def test_job_artifact_service_falls_back_to_object_store_on_local_read_error(
    job_db, job, monkeypatch
):
    """read_text OSError（淘汰线程在 exists() 后 unlink 的 TOCTOU）→ 回退对象存储。"""
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")
    service = JobArtifactService(job_db, _FakeObjectStore(payload=b'{"from": "store"}'))

    def _raise_oserror(self, *args, **kwargs):
        raise OSError("evicted between exists() and read_text()")

    monkeypatch.setattr(Path, "read_text", _raise_oserror)

    result = service.read(job["id"], "result.json")

    assert result == {"name": "result.json", "content": '{"from": "store"}'}


def test_job_artifact_service_binary_local_file_does_not_500(job_db, job):
    """本地二进制产物走文本端点：UnicodeDecodeError（ValueError，非 OSError）
    以前未被捕获直接 500；现在按未找到处理（字节由 raw 端点负责）。"""
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    service = JobArtifactService(job_db, None)

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.read(job["id"], "frame.png")


def test_job_artifact_service_open_raw_local_path(job_db, job):
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "frame.png").write_bytes(b"\x89PNG-bytes")
    service = JobArtifactService(job_db, None)

    raw = service.open_raw(job["id"], "frame.png")

    assert raw.name == "frame.png"
    assert raw.path is not None
    assert raw.path.read_bytes() == b"\x89PNG-bytes"
    assert raw.stream is None


def test_job_artifact_service_open_raw_local_wins_over_object_store(job_db, job):
    """本地 job_dir 与对象存储同时有副本时，本地文件优先（与 read() 同序）。"""
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "frame.png").write_bytes(b"local-bytes")
    service = JobArtifactService(job_db, _FakeObjectStore(payload=b"store-bytes"))

    raw = service.open_raw(job["id"], "frame.png")

    assert raw.path is not None
    assert raw.path.read_bytes() == b"local-bytes"
    assert raw.stream is None


def test_job_artifact_service_open_raw_object_stream(job_db, job):
    """本地缓存已淘汰 → 对象存储流式输出（带 manifest 的 size_bytes）。"""
    service = JobArtifactService(job_db, _FakeObjectStore(payload=b"\x00\x01binary"))

    raw = service.open_raw(job["id"], "result.json")

    assert raw.stream is not None
    assert raw.stream.read() == b"\x00\x01binary"
    assert raw.path is None
    assert raw.size_bytes == len(b"\x00\x01binary")


def test_job_artifact_service_open_raw_missing(job_db, job):
    service = JobArtifactService(job_db, None)

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.open_raw(job["id"], "missing.png")


def test_job_artifact_service_open_raw_object_error_is_404(job_db, job):
    """对象存储故障 → 404 而非 500（对齐 read() 的降级语义）。"""
    from botocore.exceptions import ClientError

    boto_outage = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    service = JobArtifactService(job_db, _FakeObjectStore(error=boto_outage))

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.open_raw(job["id"], "result.json")


def test_job_artifact_service_open_raw_programming_error_propagates(job_db, job):
    """#204 窄化：raw 端点只降级 boto 数据面故障族；注入的编程错误
    （TypeError）原样上抛给路由层 500，不再被吞成 404。"""
    service = JobArtifactService(
        job_db, _FakeObjectStore(error=TypeError("store contract violation"))
    )

    with pytest.raises(TypeError, match="store contract violation"):
        service.open_raw(job["id"], "result.json")


def test_job_artifact_service_read_object_programming_error_propagates(job_db, job):
    """#204 窄化：read() 的对象存储回退同样只降级声明的失败族
    （ClientError/BotoCoreError/OSError/UnicodeDecodeError）。"""
    service = JobArtifactService(job_db, _FakeObjectStore(error=TypeError("bad double")))

    with pytest.raises(TypeError, match="bad double"):
        service.read(job["id"], "result.json")


def test_job_artifact_service_open_raw_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.open_raw(job["id"], "../agent_legion.sqlite")


class _FakeRangedObjectStore:
    """记录 open_range_stream 调用区间的对象存储 double。"""

    enabled = True

    def __init__(self, payload: bytes):
        self._payload = payload
        self.range_calls: list[tuple[int, int]] = []

    def lookup(self, job_id: str, name: str) -> dict:
        return {
            "storage_key": f"jobs/ws/{job_id}/{name}",
            "size_bytes": len(self._payload),
        }

    def open_stream(self, row: dict) -> io.BytesIO:
        return io.BytesIO(self._payload)

    def open_range_stream(self, row: dict, start: int, end: int) -> io.BytesIO:
        self.range_calls.append((start, end))
        return io.BytesIO(self._payload[start : end + 1])


def test_job_artifact_service_open_raw_ranged(job_db, job):
    """Range 请求走 open_range_stream（闭区间），流分支 seek 可用。"""
    store = _FakeRangedObjectStore(b"0123456789")
    service = JobArtifactService(job_db, store)

    raw = service.open_raw(job["id"], "clip.mp4", range_header="bytes=2-5")

    assert raw.stream is not None
    assert raw.stream.read() == b"2345"
    assert raw.size_bytes == 10
    assert raw.range_start == 2
    assert raw.range_end == 5
    assert store.range_calls == [(2, 5)]


def test_job_artifact_service_open_raw_no_range_uses_full_stream(job_db, job):
    """无 Range 参数仍走 open_stream 全量（不误入 ranged 分支）。"""
    store = _FakeRangedObjectStore(b"0123456789")
    service = JobArtifactService(job_db, store)

    raw = service.open_raw(job["id"], "clip.mp4")

    assert raw.stream is not None
    assert raw.stream.read() == b"0123456789"
    assert store.range_calls == []


# --- #338：.gz 对象双形态读（真实 JobArtifactObjectStore + 内存存储） --------

_GZ_RAW = b'{"gz": true, "items": [1, 2, 3]}'
_GZ_COMPRESSED = gzip.compress(_GZ_RAW)
_GZ_HASH = hashlib.sha256(_GZ_RAW).hexdigest()


def _seed_gz_row(job_db, job) -> JobArtifactObjectStore:
    """登记一条 .gz 形态的产物行（storage_key 带后缀、size=压缩、hash=未压缩）。"""
    workspace_id = job["workspace_id"]
    storage_key = f"jobs/{workspace_id}/{job['id']}/result.json.gz"
    storage = FakeObjectStorage(objects={storage_key: _GZ_COMPRESSED})
    store = JobArtifactObjectStore(job_db, storage)
    store.record_remote(
        workspace_id=workspace_id,
        job_id=job["id"],
        node_key="upstream",
        name="result.json",
        storage_key=storage_key,
        size_bytes=len(_GZ_COMPRESSED),
        content_hash=_GZ_HASH,
    )
    return store


def test_read_object_gunzips_gz_object(job_db, job):
    """文本预览：.gz 对象透明解压后 decode（本地无缓存副本，走对象分支）。"""
    service = JobArtifactService(job_db, _seed_gz_row(job_db, job))

    result = service.read(job["id"], "result.json")

    assert result == {"name": "result.json", "content": _GZ_RAW.decode("utf-8")}


def test_open_raw_gz_object_passthrough_with_encoding(job_db, job):
    """raw 端点：.gz 对象按存储字节透传 + content_encoding 标记；Range 请求
    被忽略（gzip 流不支持分段解码），size_bytes 是压缩后字节数。"""
    store = _seed_gz_row(job_db, job)
    service = JobArtifactService(job_db, store)

    raw = service.open_raw(job["id"], "result.json", range_header="bytes=0-3")

    assert raw.stream is not None
    assert raw.stream.read() == _GZ_COMPRESSED  # 透传压缩字节（全量）
    assert raw.content_encoding == "gzip"
    assert raw.size_bytes == len(_GZ_COMPRESSED)
    assert raw.range_start is None and raw.range_end is None


def test_object_store_open_stream_dual_form(job_db, job):
    """store 层契约：open_stream 对 .gz 透明解压、对裸对象原样；
    open_object_stream 永远返回存储字节。"""
    store = _seed_gz_row(job_db, job)
    row = store.lookup(job["id"], "result.json")
    assert row is not None

    with store.open_stream(row) as stream:
        assert stream.read() == _GZ_RAW
    with store.open_object_stream(row) as stream:
        assert stream.read() == _GZ_COMPRESSED

    # 裸形态行（存量数据）：open_stream 原样返回。
    bare_key = f"jobs/{job['workspace_id']}/{job['id']}/bare.json"
    store.storage.objects[bare_key] = _GZ_RAW
    store.record_remote(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key="upstream",
        name="bare.json",
        storage_key=bare_key,
        size_bytes=len(_GZ_RAW),
        content_hash=_GZ_HASH,
    )
    bare_row = store.lookup(job["id"], "bare.json")
    assert bare_row is not None
    with store.open_stream(bare_row) as stream:
        assert stream.read() == _GZ_RAW
