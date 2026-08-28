import io
from pathlib import Path

import pytest

from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir


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
        return {"storage_key": f"jobs/ws/{job_id}/{name}"}

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
    service = JobArtifactService(job_db, _FakeObjectStore(error=RuntimeError("NoSuchKey")))

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
    assert not raw.is_stream


def test_job_artifact_service_open_raw_object_stream(job_db, job):
    """本地缓存已淘汰 → 对象存储流式输出。"""
    service = JobArtifactService(job_db, _FakeObjectStore(payload=b"\x00\x01binary"))

    raw = service.open_raw(job["id"], "result.json")

    assert raw.is_stream
    assert raw.stream is not None
    assert raw.stream.read() == b"\x00\x01binary"
    assert raw.path is None


def test_job_artifact_service_open_raw_missing(job_db, job):
    service = JobArtifactService(job_db, None)

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.open_raw(job["id"], "missing.png")


def test_job_artifact_service_open_raw_object_error_is_404(job_db, job):
    """对象存储故障 → 404 而非 500（对齐 read() 的降级语义）。"""
    service = JobArtifactService(job_db, _FakeObjectStore(error=RuntimeError("NoSuchKey")))

    with pytest.raises(NotFoundError, match="Artifact not found"):
        service.open_raw(job["id"], "result.json")


def test_job_artifact_service_open_raw_rejects_traversal(artifact_service, job):
    with pytest.raises(InvalidOperationError, match="Invalid artifact name"):
        artifact_service.open_raw(job["id"], "../agent_legion.sqlite")
