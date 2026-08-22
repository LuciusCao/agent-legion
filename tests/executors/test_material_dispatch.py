"""Host dispatch 接线：material 输入经 build_runtime 物化并注入 runtime。

MATERIAL-ACCESS-001：沙箱 allow-read 只含静态缓存根；物化失败映射为可读的
节点失败信息（不经沙箱崩溃）。
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path

import pytest

from server.app.executors._code_runtime import build_runtime
from server.app.executors._code_sandbox import _read_roots
from server.app.executors.cancellation import CancellationToken
from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext
from server.app.storage import ObjectHead
from shared.material_cache import MaterializeError

WORKSPACE_ID = "ws-mat-dispatch"
PAYLOAD = b"dispatch-material" * 40
HASH = hashlib.sha256(PAYLOAD).hexdigest()
MATERIAL_ID = "mat-dispatch-1"
STORAGE_KEY = f"{WORKSPACE_ID}/{HASH}/input.csv"


class FakeStorage:
    def __init__(self) -> None:
        self.objects = {STORAGE_KEY: PAYLOAD}

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return ""

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        return ""

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


@pytest.fixture
def material(job_db):
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'MatDispatch', 'demo_workflow') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into materials("
            " id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by"
            ") values (%s, %s, %s, 'input.csv', 'text/csv', %s, %s, 'ready', 'user-1')",
            (MATERIAL_ID, WORKSPACE_ID, HASH, len(PAYLOAD), STORAGE_KEY),
        )
    return MATERIAL_ID


def _executor(job_db, tmp_path: Path, storage: FakeStorage) -> CodeExecutor:
    executor = CodeExecutor(
        repo_root=tmp_path,
        job_db=job_db,
        materials_cache_root=tmp_path / "materials_cache",
    )
    executor._storage_probed = True
    executor._object_storage = storage
    return executor


def _context(tmp_path: Path, input_doc: dict | None) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-mat",
        lease_id="lease-1",
        node_run_id=1,
        executor_id="code",
        workspace_id=WORKSPACE_ID,
        job_id="job-mat",
        workflow_key="wf",
        node_key="intake",
        capability="intake_items",
        workspace={"id": WORKSPACE_ID},
        job={
            "id": "job-mat",
            "workspace_id": WORKSPACE_ID,
            "run_id": "",
            "input_json": json.dumps(input_doc) if input_doc else "",
        },
        job_dir=tmp_path / "job",
        log_path=tmp_path / "job" / "run.log",
        inputs=(),
        expected_outputs=(),
    )


def _runtime(executor: CodeExecutor, context: ExecutionContext) -> dict:
    return build_runtime(executor, context, CancellationToken(threading.Event()))


def test_build_runtime_materializes_and_injects_block(job_db, material, tmp_path: Path) -> None:
    executor = _executor(job_db, tmp_path, FakeStorage())
    context = _context(tmp_path, {"type": "material", "material_id": material})

    runtime = _runtime(executor, context)

    block = runtime["materials"]
    assert block["material_id"] == material
    assert block["filename"] == "input.csv"
    path = Path(block["path"])
    assert path.is_file()
    assert path.read_bytes() == PAYLOAD
    assert path.is_relative_to(tmp_path / "materials_cache")


def test_build_runtime_skips_non_material(job_db, tmp_path: Path) -> None:
    executor = _executor(job_db, tmp_path, FakeStorage())
    context = _context(tmp_path, {"type": "ref", "external_id": "q-1"})

    assert "materials" not in _runtime(executor, context)


def test_build_runtime_raises_readable_error_without_storage(
    job_db, material, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _executor(job_db, tmp_path, FakeStorage())
    executor._object_storage = None  # 实例未配置对象存储
    monkeypatch.setattr("server.app.services.material_cache.build_s3_storage", lambda: None)
    context = _context(tmp_path, {"type": "material", "material_id": material})

    with pytest.raises(MaterializeError, match="storage is not configured"):
        _runtime(executor, context)


def test_sandbox_read_roots_include_static_cache_root(job_db, tmp_path: Path) -> None:
    executor = _executor(job_db, tmp_path, FakeStorage())
    cache_root = tmp_path / "materials_cache"

    assert str(cache_root) not in _read_roots(executor), "不存在的目录不进白名单"
    cache_root.mkdir()
    assert str(cache_root) in _read_roots(executor)
