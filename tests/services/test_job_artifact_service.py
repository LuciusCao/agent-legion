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
    (e.g. NoSuchKey after a bucket lifecycle deletion).

    #631 攻击复审 H1 后 storage_key 必须落在本 job 前缀
    （jobs/{workspace}/{job_id}/…）内，读侧兜底才放行——double 的 key
    从传入 job 行派生 workspace，而不是假名。"""

    enabled = True

    def __init__(self, payload: bytes = b"", error: Exception | None = None):
        self._payload = payload
        self._error = error
        self._workspace_id = "default"

    def bind_workspace(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id

    def lookup(self, job_id: str, name: str) -> dict:
        return {
            "storage_key": f"jobs/{self._workspace_id}/{job_id}/{name}",
            "size_bytes": len(self._payload),
        }

    def open_stream(self, row: dict) -> io.BytesIO:
        if self._error is not None:
            raise self._error
        return io.BytesIO(self._payload)


def test_job_artifact_service_reads_from_object_store(job_db, job):
    """本地缓存已淘汰时从对象存储回读成功。"""
    service = JobArtifactService(job_db, _fake_store(job, payload=b'{"from": "store"}'))

    result = service.read(job["id"], "result.json")

    assert result == {"name": "result.json", "content": '{"from": "store"}'}


def test_job_artifact_service_object_error_becomes_404(job_db, job):
    """对象被 lifecycle 删除 / 存储故障 → 按未找到处理（404），不冒泡 500。"""
    from botocore.exceptions import ClientError

    boto_outage = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    service = JobArtifactService(job_db, _fake_store(job, error=boto_outage))

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.read(job["id"], "result.json")


def test_job_artifact_service_falls_back_to_object_store_on_local_read_error(
    job_db, job, monkeypatch
):
    """read_text OSError（淘汰线程在 exists() 后 unlink 的 TOCTOU）→ 回退对象存储。"""
    storage = resolve_job_dir(job, job_db.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "result.json").write_text('{"ok": true}', encoding="utf-8")
    service = JobArtifactService(job_db, _fake_store(job, payload=b'{"from": "store"}'))

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
    service = JobArtifactService(job_db, _fake_store(job, payload=b"store-bytes"))

    raw = service.open_raw(job["id"], "frame.png")

    assert raw.path is not None
    assert raw.path.read_bytes() == b"local-bytes"
    assert raw.stream is None


def test_job_artifact_service_open_raw_object_stream(job_db, job):
    """本地缓存已淘汰 → 对象存储流式输出（带 manifest 的 size_bytes）。"""
    service = JobArtifactService(job_db, _fake_store(job, payload=b"\x00\x01binary"))

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
    service = JobArtifactService(job_db, _fake_store(job, error=boto_outage))

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.open_raw(job["id"], "result.json")


def test_job_artifact_service_open_raw_programming_error_propagates(job_db, job):
    """#204 窄化：raw 端点只降级 boto 数据面故障族；注入的编程错误
    （TypeError）原样上抛给路由层 500，不再被吞成 404。"""
    service = JobArtifactService(
        job_db, _fake_store(job, error=TypeError("store contract violation"))
    )

    with pytest.raises(TypeError, match="store contract violation"):
        service.open_raw(job["id"], "result.json")


def test_job_artifact_service_read_object_programming_error_propagates(job_db, job):
    """#204 窄化：read() 的对象存储回退同样只降级声明的失败族
    （ClientError/BotoCoreError/OSError/UnicodeDecodeError）。"""
    service = JobArtifactService(job_db, _fake_store(job, error=TypeError("bad double")))

    with pytest.raises(TypeError, match="bad double"):
        service.read(job["id"], "result.json")


def test_job_artifact_service_open_raw_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.open_raw(job["id"], "../agent_legion.sqlite")


# --- #631 攻击复审 M1/M2：下载侧名字白名单 ------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 合法形态：普通名、子路径名、200 字节段（上限内）。
        ("result.json", True),
        ("reports/final.json", True),
        ("a/b/c/d.txt", True),
        ("x" * 200, True),
        ("é" * 100, True),  # 200 bytes UTF-8
        # runs/ 与点前缀段：与 artifact_names_deep 的清单剪枝同一规则。
        ("runs", False),
        ("runs/node_a/events.jsonl", False),
        ("reports/runs/final.json", False),
        (".trash/leak.txt", False),
        ("a/.hidden/b", False),
        ("..", False),
        (".", False),
        ("", False),
        # 控制字符（含 NUL）与 DEL。
        ("a\x00b", False),
        ("a\nb", False),
        ("a\x1bb", False),
        ("a\x7fb", False),
        # 反斜杠（Windows 风格穿越）与超长段。
        ("x\\y", False),
        ("..\\..\\etc", False),
        ("x" * 201, False),
        ("é" * 101, False),  # 202 bytes
        # 绝对名。
        ("/abs/path", False),
    ],
)
def test_is_downloadable_artifact_name_matrix(name, expected):
    """白名单矩阵（单一事实来源 job_artifact_names）：清单剪枝规则 +
    控制字符/超长段/反斜杠拒绝。"""
    from server.app.services.job_artifact_names import is_downloadable_artifact_name

    assert is_downloadable_artifact_name(name) is expected


@pytest.mark.parametrize(
    "name",
    [
        "runs/node_a/events.jsonl",
        ".trash/leak.txt",
        "a\x00b",
        "x" * 300,
        "x" * 201,
    ],
)
def test_job_artifact_service_rejects_non_artifact_names(artifact_service, job, name):
    """serve 侧（read/open_raw/open_raw_current 共用 _artifact_path）对
    清单不会列出的名字一律 InvalidOperationError（400），不再是「文件
    存在即可达」。"""
    for call in (
        lambda: artifact_service.read(job["id"], name),
        lambda: artifact_service.open_raw(job["id"], name),
        lambda: artifact_service.open_raw_current(job["id"], name),
    ):
        with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
            call()


@pytest.mark.parametrize("job_id", ["a\x00b", "a\nb", "a\x7fb"])
def test_job_artifact_service_rejects_control_char_job_ids(artifact_service, job_id):
    """#631 攻击复审 M1：控制字符 job_id 在 SQL 参数化时炸 psycopg
    DataError（500）；形状早拒为 InvalidOperationError（400）。"""
    with pytest.raises(InvalidOperationError, match="Invalid job id"):
        artifact_service.read(job_id, "result.json")


class _FakeRangedObjectStore:
    """记录 open_range_stream 调用区间的对象存储 double（key 同样落在本
    job 前缀内——读侧兜底语义，见 _FakeObjectStore 注释）。"""

    enabled = True

    def __init__(self, payload: bytes):
        self._payload = payload
        self.range_calls: list[tuple[int, int]] = []
        self._workspace_id = "default"

    def bind_workspace(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id

    def lookup(self, job_id: str, name: str) -> dict:
        return {
            "storage_key": f"jobs/{self._workspace_id}/{job_id}/{name}",
            "size_bytes": len(self._payload),
        }

    def open_stream(self, row: dict) -> io.BytesIO:
        return io.BytesIO(self._payload)

    def open_range_stream(self, row: dict, start: int, end: int) -> io.BytesIO:
        self.range_calls.append((start, end))
        return io.BytesIO(self._payload[start : end + 1])


def _fake_store(job: dict, **kwargs) -> _FakeObjectStore:
    """_FakeObjectStore bound to the job's real workspace (H1 read-side prefix
    guard requires in-prefix keys)."""
    store = _FakeObjectStore(**kwargs)
    store.bind_workspace(str(job["workspace_id"]))
    return store


def _fake_ranged_store(job: dict, payload: bytes) -> _FakeRangedObjectStore:
    store = _FakeRangedObjectStore(payload)
    store.bind_workspace(str(job["workspace_id"]))
    return store


def test_job_artifact_service_open_raw_ranged(job_db, job):
    """Range 请求走 open_range_stream（闭区间），流分支 seek 可用。"""
    store = _fake_ranged_store(job, b"0123456789")
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
    store = _fake_ranged_store(job, b"0123456789")
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


# --- #631 攻击复审 H1：读侧 storage_key 前缀兜底 -------------------------------


def test_open_raw_row_refuses_key_outside_job_prefix(job_db, job):
    """H1（对象分支）：manifest 行指向其他 workspace/其他 job 的对象 key
    时，open_raw_row 按 NotFound 处理——行是读路径唯一权威，读侧兜底让
    写歪的行（未来写入方失守、运维 SQL 误操作）不读穿 workspace 边界。"""
    from server.app.services.job_artifact_raw import open_raw_row

    store = JobArtifactObjectStore(job_db, FakeObjectStorage(objects={}))
    foreign_key = f"jobs/other-ws/{job['id']}/result.json.gz"
    store.storage.objects[foreign_key] = gzip.compress(b"FOREIGN")
    row = {
        "job_id": job["id"],
        "node_key": "upstream",
        "name": "result.json",
        "storage_key": foreign_key,
        "size_bytes": 42,
        "content_hash": "",
    }

    with pytest.raises(NotFoundError, match="Artifact not found"):
        open_raw_row(store, row, "result.json", job=job)


def test_open_raw_row_serves_key_within_job_prefix(job_db, job):
    """兜底不误伤：本 job 前缀内的行（.gz 与裸 key）照常打开。"""
    from server.app.services.job_artifact_raw import open_raw_row

    store = _seed_gz_row(job_db, job)
    row = store.lookup(job["id"], "result.json")
    assert row is not None

    raw = open_raw_row(store, row, "result.json", job=job)
    assert raw.stream is not None
    assert gzip.decompress(raw.stream.read()) == _GZ_RAW

    bare_key = f"jobs/{job['workspace_id']}/{job['id']}/bare.json"
    store.storage.objects[bare_key] = _GZ_RAW
    bare_row = {
        "job_id": job["id"],
        "node_key": "upstream",
        "name": "bare.json",
        "storage_key": bare_key,
        "size_bytes": len(_GZ_RAW),
        "content_hash": "",
    }
    bare = open_raw_row(store, bare_row, "bare.json", job=job)
    assert bare.stream is not None
    assert bare.stream.read() == _GZ_RAW
