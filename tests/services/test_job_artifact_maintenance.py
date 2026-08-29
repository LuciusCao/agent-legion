"""Job artifact maintenance：reconciler 行新鲜度 + 淘汰 size 防线（#160 P1-2）。

rerun 产出新字节而上传失败时，旧 (job_id,node_key,name) 清单行不得永久
压制重传（reconciler 比对 size+hash 后 upsert 刷新），也不得过凭旧行
淘汰本地新文件（eviction 只认 size 相符的行）。
"""

from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services import job_artifact_maintenance
from server.app.services.job_artifact_maintenance import (
    evict_cache_to_capacity,
    reupload_missing,
)
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.storage import ObjectHead
from tests.postgres_support import TEST_DATABASE_URL

OLD_PAYLOAD = b"old-bytes"
NEW_PAYLOAD = b"new-bytes-longer"


class FakeStorage:
    """In-memory ObjectStorage test double; never touches the network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def presign_put(self, storage_key: str, size_bytes: int, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/upload/{storage_key}"

    def presign_get(self, storage_key: str, expires_seconds: int = 3600) -> str:
        return f"https://s3.test/download/{storage_key}"

    def head_object(self, storage_key: str) -> ObjectHead | None:
        payload = self.objects.get(storage_key)
        return None if payload is None else ObjectHead(size_bytes=len(payload))

    def open_stream(self, storage_key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[storage_key])

    def put_object(self, storage_key: str, data: bytes, content_type: str = "") -> None:
        self.objects[storage_key] = data

    def put_stream(self, storage_key: str, stream: BinaryIO, size_bytes: int) -> None:
        self.objects[storage_key] = stream.read()

    def delete_object(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)

    def copy_object(self, source_key: str, destination_key: str) -> None:
        self.objects[destination_key] = self.objects[source_key]


@pytest.fixture(autouse=True)
def _schema() -> None:
    init_db(TEST_DATABASE_URL)


def _seed_job(status: str = "completed") -> dict[str, Any]:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-1', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values"
            " ('job-1', 'ws-1', 'wf', 's', 's1', 't', %s, 'jobs/ws/job-1')",
            (status,),
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, finished_at)"
            " values ('job-1', 'n1', 'completed', now())"
        )
    return {
        "id": "job-1",
        "workspace_id": "ws-1",
        "workflow_key": "wf",
        "storage_dir": "jobs/ws/job-1",
    }


def _job_db(job: dict[str, Any]) -> Any:
    # The service reads job/manifest rows through the JobQueries read facade
    # (#187); the stub exposes a real read connection to the test database
    # alongside the canned get_job.
    @contextmanager
    def _read():
        with read_connection(TEST_DATABASE_URL) as conn:
            yield conn

    return SimpleNamespace(path=TEST_DATABASE_URL, get_job=lambda job_id: dict(job), read=_read)


def _settings(tmp_path: Path) -> Any:
    return SimpleNamespace(jobs_dir=tmp_path / "jobs")


def _definition(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = SimpleNamespace(nodes={"n1": SimpleNamespace(outputs=["out.json"])})
    monkeypatch.setattr(
        job_artifact_maintenance, "definition_from_job_snapshot", lambda job: definition
    )


def test_reconciler_reuploads_when_row_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rerun 新字节 + 旧行（size 不同）→ 重传并 upsert 刷新清单行。"""
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )
    local.write_bytes(NEW_PAYLOAD)  # rerun 产出新字节，上传失败 → 行过期

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 1

    row = store.row_for_node("job-1", "n1", "out.json")
    assert row is not None
    assert int(row["size_bytes"]) == len(NEW_PAYLOAD)
    assert row["content_hash"] == hashlib.sha256(NEW_PAYLOAD).hexdigest()
    assert storage.objects["jobs/ws-1/job-1/out.json"] == NEW_PAYLOAD


def test_reconciler_reuploads_on_same_size_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(b"aaaa")
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )
    local.write_bytes(b"bbbb")  # 同长度新字节：只有 hash 能识别过期

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 1
    assert storage.objects["jobs/ws-1/job-1/out.json"] == b"bbbb"


def test_reconciler_skips_fresh_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _seed_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 0


def _complete_job() -> dict[str, Any]:
    return _seed_job(status="completed")


def test_eviction_skips_files_whose_size_no_longer_matches(tmp_path: Path) -> None:
    """行在但 size 不符（rerun 新字节未上传）→ 不视为已确认，不淘汰。"""
    job = _complete_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    fresh = job_dir / "out.json"
    fresh.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=fresh
    )
    stale = job_dir / "stale.json"
    stale.write_bytes(b"old")
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="stale.json", local_path=stale
    )
    stale.write_bytes(b"new-longer")  # 行 size=3，本地 size=10 → 不确认

    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 1  # 只有 size 相符的 out.json 被淘汰
    assert not fresh.exists()
    assert stale.is_file()


def test_eviction_removes_confirmed_files(tmp_path: Path) -> None:
    """行有 hash 且本地文件匹配 → 正常淘汰（upload 始终记录 content_hash）。"""
    job = _complete_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    confirmed = job_dir / "out.json"
    confirmed.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=confirmed
    )

    assert evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0) == 1
    assert not confirmed.exists()


def test_eviction_skips_same_size_hash_mismatch_when_reupload_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """核心回归：rerun 同长度新字节 + reconciler 重传再失败 → 旧行的 size
    相符不得认证新字节，淘汰必须跳过，否则新字节永久丢失。"""
    job = _complete_job()
    _definition(monkeypatch)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    local = job_dir / "out.json"
    local.write_bytes(b"aaaa")
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=local
    )
    local.write_bytes(b"bbbb")  # 同长度新字节，行 size 仍相符但 hash 不符

    def _fail_upload(**kwargs: Any) -> None:
        raise RuntimeError("storage down")

    monkeypatch.setattr(store, "upload", _fail_upload)
    reupload_missing(store, _job_db(job), _settings(tmp_path))  # 重传失败被吞
    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 0
    assert local.read_bytes() == b"bbbb"


def _add_active_lease() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        run_id = conn.execute("select id from node_runs where job_id='job-1'").fetchone()["id"]
        conn.execute(
            "insert into executor_leases("
            " id, execution_id, executor_id, workspace_id, job_id, workflow_key,"
            " node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values ('lease-1', 'exec-1', 'code', 'ws-1', 'job-1', 'wf', 'n1', %s,"
            " 'active', now(), now(), now() + make_interval(hours => 1))",
            (run_id,),
        )


def _confirmed_cache_file(tmp_path: Path) -> tuple[JobArtifactObjectStore, Path]:
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    confirmed = job_dir / "out.json"
    confirmed.write_bytes(OLD_PAYLOAD)
    store.upload(
        workspace_id="ws-1", job_id="job-1", node_key="n1", name="out.json", local_path=confirmed
    )
    return store, confirmed


def test_eviction_skips_job_with_active_lease(tmp_path: Path) -> None:
    """job 持有 active lease（可能正在 rerun 写新产物）→ 不淘汰。"""
    job = _complete_job()
    _add_active_lease()
    store, confirmed = _confirmed_cache_file(tmp_path)

    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 0
    assert confirmed.is_file()


def test_eviction_skips_non_completed_job(tmp_path: Path) -> None:
    """非 completed 的 job（queued/running 可能重跑节点）→ 不淘汰。"""
    job = _seed_job(status="queued")
    store, confirmed = _confirmed_cache_file(tmp_path)

    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 0
    assert confirmed.is_file()


def test_eviction_rechecks_precondition_before_every_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unlink 间隙前提失效（job 被 rerun 置回 queued）→ 同 job 的后续文件
    不得再删（逐文件重查，不按 job 缓存结论）。"""
    job = _complete_job()
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    for name in ("a.json", "b.json"):
        local = job_dir / name
        local.write_bytes(OLD_PAYLOAD)
        store.upload(
            workspace_id="ws-1", job_id="job-1", node_key="n1", name=name, local_path=local
        )

    calls = 0

    def _flip(job_db: Any, job_id: str) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1  # 仅第一次重查成立，之后前提失效

    monkeypatch.setattr(job_artifact_maintenance, "_job_still_evictable", _flip)

    evicted = evict_cache_to_capacity(store, _job_db(job), _settings(tmp_path), max_bytes=0)

    assert evicted == 1  # 前提失效后同 job 的第二个文件被跳过
    assert calls == 2
    remaining = [path for path in job_dir.iterdir() if path.is_file()]
    assert len(remaining) == 1


def test_reconciler_skips_job_without_active_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#204 窄化：workspace 无 active revision（NotFoundError）→ 跳过该 job、
    pass 继续（返回 0 而不是上抛）。"""
    job = _seed_job()
    # 无快照 + 无 active revision → require_workspace_active_definition 抛 NotFoundError
    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", lambda job: None)

    job_db = _job_db(job)
    job_db.get_active_workflow_revision = lambda ws, key: None

    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    assert reupload_missing(store, job_db, _settings(tmp_path)) == 0


def test_reconciler_skips_job_with_unmappable_storage_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#204 窄化：storage_dir 无法映射进 data 根（ManagedPathError）→ 跳过。"""
    job = _seed_job()
    _definition(monkeypatch)
    job["storage_dir"] = "/etc/passwd"  # 绝对路径无法回基到 data 根内
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 0


def test_reconciler_propagates_programming_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#204 窄化：定义解析抛出非预期编程错误（TypeError）→ 上抛给线程保命网，
    不再被静默吞成「本 pass 零上传」。"""
    job = _seed_job()

    def _boom(job: dict[str, Any]) -> Any:
        raise TypeError("parser bug")

    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", _boom)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    with pytest.raises(TypeError, match="parser bug"):
        reupload_missing(store, _job_db(job), _settings(tmp_path))


def test_reconciler_survives_upload_failure_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#204 保留补强：单产物上传失败（存储异常）→ warning 日志、其余产物
    继续上传，pass 不中断（reconciler 即重试机制）。"""
    job = _seed_job()
    definition = SimpleNamespace(nodes={"n1": SimpleNamespace(outputs=["a.json", "b.json"])})
    monkeypatch.setattr(
        job_artifact_maintenance, "definition_from_job_snapshot", lambda job: definition
    )
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    job_dir = tmp_path / "jobs" / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "a.json").write_bytes(OLD_PAYLOAD)
    (job_dir / "b.json").write_bytes(OLD_PAYLOAD)
    real_upload = store.upload

    def _flaky_upload(**kwargs: Any) -> Any:
        if kwargs["name"] == "a.json":
            raise RuntimeError("storage outage")
        return real_upload(**kwargs)

    monkeypatch.setattr(store, "upload", _flaky_upload)

    with caplog.at_level("WARNING", logger="server.app.services.job_artifact_maintenance"):
        assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 1

    # b.json 上传成功；a.json 的失败被 warning 记录（含 job 上下文）
    assert storage.objects["jobs/ws-1/job-1/b.json"] == OLD_PAYLOAD
    assert "jobs/ws-1/job-1/a.json" not in storage.objects
    assert any("reconciler re-upload failed" in record.message for record in caplog.records)


def test_reconciler_skips_job_with_corrupt_active_revision_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review on PR #251: an active revision whose definition_json is
    corrupt (JSONDecodeError / WorkflowDefinitionError — the #243 family) must
    skip that job, not abort the reconciler pass (which would also stall
    eviction for every other job)."""

    class _CorruptRevision(dict):
        pass

    job = _seed_job()
    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", lambda job: None)
    job_db = _job_db(job)
    # definition_json 不是合法 JSON → json.loads 抛 JSONDecodeError
    job_db.get_active_workflow_revision = lambda ws, key: {"definition_json": "{not valid json"}

    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    assert reupload_missing(store, job_db, _settings(tmp_path)) == 0


def test_reconciler_skips_job_with_non_mapping_revision_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-2 on PR #251: a revision whose definition_json parses as
    valid JSON but a non-mapping top level ([], null) used to escape the
    narrow catch as AttributeError (payload.get) and abort the whole pass —
    now the shape guard in snapshot_shape converts it to
    WorkflowDefinitionError and the job is skipped."""
    job = _seed_job()
    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", lambda job: None)
    job_db = _job_db(job)
    job_db.get_active_workflow_revision = lambda ws, key: {"definition_json": "[]"}

    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    assert reupload_missing(store, job_db, _settings(tmp_path)) == 0


def test_reconciler_propagates_parser_valueerror_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-3 on PR #251: a plain ValueError from a definition-parser
    bug must propagate (the job's artifacts must not be silently stranded),
    unlike the #243-family JSON corruption which skips per-job."""
    job = _seed_job()
    monkeypatch.setattr(job_artifact_maintenance, "definition_from_job_snapshot", lambda job: None)

    def _boom(job_db, workspace_id, workflow_key):
        raise ValueError("parser implementation bug")

    monkeypatch.setattr(job_artifact_maintenance, "require_workspace_active_definition", _boom)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    with pytest.raises(ValueError, match="parser implementation bug"):
        reupload_missing(store, _job_db(job), _settings(tmp_path))


def test_reconciler_skips_job_on_os_resolve_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #251 review P2-2: _PATH_FAILURES (OSError/RuntimeError from
    resolve_job_dir — permissions, symlink loops) must skip just that job;
    narrowing it back to ManagedPathError alone would turn red here."""
    job = _seed_job()
    _definition(monkeypatch)

    def _boom(job, jobs_dir):
        raise RuntimeError("symlink loop detected")

    monkeypatch.setattr(job_artifact_maintenance, "resolve_job_dir", _boom)
    storage = FakeStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)

    assert reupload_missing(store, _job_db(job), _settings(tmp_path)) == 0
