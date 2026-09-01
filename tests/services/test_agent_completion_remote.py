"""AgentCompletionHandler 接收 Worker 直传 S3 的产物引用（#160 D12）。

Worker 直传到每次 execution 唯一的 staging key（jobs-staging/...）；Host
先全部核验（staging 布局绑定本 execution、HEAD size、下载 hash），再统一
服务端 copy 提升到权威 key + 原子落盘（只落 expected_outputs 白名单）+
record_remote 登记 + best-effort 删 staging；任一失败整个 result 判
failed，且不留半应用状态。旧形态 str ref 的 add_ref 路径不变（回归由
tests/services/test_agent_completion_validation.py 覆盖）。

#338 双形态：.gz staging ref（v4+ worker 压缩上传）HEAD 按压缩字节数核
验、下载透明解压落 job_dir、content_hash 按未压缩字节；裸 staging ref
（旧 worker）路径不变。形态切换的重跑（raw → gzip）authority key 随之带
后缀，旧形态对象留存给仍指向它的清单行直到单事务 retarget。
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
from typing import Any

import pytest
from psycopg import IntegrityError

from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome
from server.app.db.schema import init_db
from server.app.db.transaction import write_transaction
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL

PAYLOAD = b"remote-artifact-bytes"
HASH = hashlib.sha256(PAYLOAD).hexdigest()
STAGING_KEY = "jobs-staging/ws-1/job-1/exec-1/out.json"
AUTHORITY_KEY = "jobs/ws-1/job-1/out.json"

FakeStorage = FakeObjectStorage


class _StubJobDb:
    def __init__(self, job: dict[str, Any]) -> None:
        self._job = job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._job if job_id == self._job["id"] else None


class _StubLeases:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job_db = _StubJobDb(job)
        self.data_dir = None
        self.results: list[Any] = []

    def finish(self, lease_id: str, result: Any) -> bool:
        self.results.append(result)
        return True


class _StubArtifactStore:
    def __init__(self) -> None:
        self.refs: list[tuple[str, str, str, str]] = []

    def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
        self.refs.append((job_id, node_key, name, digest))


def _staging_key(name: str, execution_id: str = "exec-1") -> str:
    return f"jobs-staging/ws-1/job-1/{execution_id}/{name}"


def _remote_ref(key: str = STAGING_KEY, size: int | None = None, content_hash: str = HASH) -> dict:
    return {
        "storage_key": key,
        "size_bytes": len(PAYLOAD) if size is None else size,
        "content_hash": content_hash,
    }


def _make_handler(
    tmp_path: Path, storage: FakeStorage | None, max_archive_bytes: int | None = None
) -> tuple[AgentCompletionHandler, _StubLeases, _StubArtifactStore, JobArtifactObjectStore, Path]:
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-1', 'ws', 'demo_workflow')"
            " on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, "
            " title, status, storage_dir) values ('job-1', 'ws-1', 's', 's1', 't', 'pending', 'd')"
        )
    jobs_dir = tmp_path / "jobs"
    job = {"id": "job-1", "workspace_id": "ws-1", "storage_dir": "jobs/ws/job-1"}
    job_dir = jobs_dir / "ws" / "job-1"
    job_dir.mkdir(parents=True)
    leases = _StubLeases(job)
    artifact_store = _StubArtifactStore()
    object_store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    handler = AgentCompletionHandler(
        leases,  # type: ignore[arg-type]
        artifact_store,  # type: ignore[arg-type]
        jobs_dir,
        tmp_path / "bundles",
        skill_manager=None,
        object_store=object_store,
        max_archive_bytes=max_archive_bytes,
    )
    return handler, leases, artifact_store, object_store, job_dir


def _finish(
    handler: AgentCompletionHandler, artifacts: dict[str, Any], *, status: str = "completed"
) -> None:
    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status=status, exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )


def test_finish_remote_ref_promotes_downloads_and_registers(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert leases.results[0].produced_artifacts == ("out.json",)
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    # 服务端 copy 提升到权威 key，staging 对象被 best-effort 删除。
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["storage_key"] == AUTHORITY_KEY
    assert row["content_hash"] == HASH
    assert artifact_store.refs == []  # 新通道不登记 CAS ref
    assert storage.put_calls == 0  # 已在 S3，不做 D12 镜像重传


def test_finish_remote_ref_missing_object_fails(tmp_path: Path) -> None:
    storage = FakeStorage()  # 对象不存在：HEAD 核验失败
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "missing" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is None


def test_finish_remote_ref_size_mismatch_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(size=len(PAYLOAD) + 1)})

    result = leases.results[0]
    assert result.status == "failed"
    assert "size" in result.error_message


def test_finish_remote_ref_stale_execution_key_fails(tmp_path: Path) -> None:
    """旧 execution 的 staging key（lease 丢失重排队后的迟发产物）被拒。"""
    storage = FakeStorage()
    storage.objects[_staging_key("out.json", execution_id="stale-exec")] = PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(key=_staging_key("out.json", "stale-exec"))})

    result = leases.results[0]
    assert result.status == "failed"
    assert "storage key" in result.error_message


def test_finish_remote_ref_authority_key_fails(tmp_path: Path) -> None:
    """dict ref 直报权威 key（绕过 staging）同样被拒。"""
    storage = FakeStorage()
    storage.objects[AUTHORITY_KEY] = PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(key=AUTHORITY_KEY)})

    result = leases.results[0]
    assert result.status == "failed"
    assert "storage key" in result.error_message


def test_finish_remote_ref_hash_mismatch_on_download_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, _, job_dir = _make_handler(tmp_path, storage)
    ref = _remote_ref(content_hash="0" * 64)  # HEAD 通过，下载字节对不上

    _finish(handler, {"out.json": ref})

    result = leases.results[0]
    assert result.status == "failed"
    assert "hash mismatch" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert AUTHORITY_KEY not in storage.objects  # 未提升


def test_finish_remote_refs_are_all_verified_before_any_apply(tmp_path: Path) -> None:
    """第二个 ref HEAD 失败时，第一个 ref 不得提升/落盘/登记（无半应用）。"""
    storage = FakeStorage()
    first_key = _staging_key("a.json")
    storage.objects[first_key] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    artifacts = {
        "a.json": _remote_ref(key=first_key),
        "out.json": _remote_ref(),  # 对象不存在
    }

    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["a.json", "out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )

    assert leases.results[0].status == "failed"
    assert not (job_dir / "a.json").exists()
    assert object_store.lookup("job-1", "a.json") is None
    assert storage.objects == {first_key: PAYLOAD}  # 无 copy 提升、无删除


def test_finish_remote_refs_hash_failure_leaves_no_partial_outputs(tmp_path: Path) -> None:
    """第二个产物下载 hash 不符：job_dir 无任何新文件、无登记、无提升。"""
    storage = FakeStorage()
    first_key = _staging_key("a.json")
    storage.objects[first_key] = PAYLOAD
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    artifacts = {
        "a.json": _remote_ref(key=first_key),
        "out.json": _remote_ref(content_hash="0" * 64),
    }

    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["a.json", "out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )

    assert leases.results[0].status == "failed"
    assert not (job_dir / "a.json").exists()
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "a.json") is None
    assert "jobs/ws-1/job-1/a.json" not in storage.objects
    assert AUTHORITY_KEY not in storage.objects


def test_finish_remote_ref_without_object_storage_fails(tmp_path: Path) -> None:
    handler, leases, _, _, _ = _make_handler(tmp_path, None)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "not configured" in result.error_message


def test_finish_cancelled_registers_without_download(tmp_path: Path) -> None:
    """cancelled run：产物登记+提升但不落 job_dir（与 tar 路径 parity）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()}, status="cancelled")

    assert leases.results[0].status == "cancelled"
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is not None
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}


def test_finish_mixed_refs_registers_both_channels(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    legacy_hash = "b" * 64
    handler, leases, artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(
        handler,
        {"out.json": _remote_ref(), "extra.json": f"sha256:{legacy_hash}"},
    )

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    assert object_store.lookup("job-1", "out.json") is not None
    assert artifact_store.refs == [("job-1", "node_a", "extra.json", legacy_hash)]


def test_finish_remote_ref_size_over_limit_fails(tmp_path: Path) -> None:
    """直传通道套用 max_archive_bytes 体积上限（与 legacy 通道 parity）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, max_archive_bytes=len(PAYLOAD) - 1
    )

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "size limit" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is None
    assert storage.objects == {STAGING_KEY: PAYLOAD}  # 未提升、未删除


def test_finish_cancelled_hash_mismatch_fails(tmp_path: Path) -> None:
    """cancelled 路径同样 digest 核验 staging 字节：自报 hash 不符整批失败。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(content_hash="0" * 64)}, status="cancelled")

    result = leases.results[0]
    assert result.status == "failed"
    assert "hash mismatch" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is None
    assert AUTHORITY_KEY not in storage.objects  # 未提升


def test_finish_cancelled_empty_hash_registers_host_computed(tmp_path: Path) -> None:
    """cancelled 且 worker 未报 hash：登记 Host 流式算出的 digest。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(content_hash="")}, status="cancelled")

    assert leases.results[0].status == "cancelled"
    assert not (job_dir / "out.json").exists()
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH  # Host 计算值，不是空串
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}


def test_finish_completed_empty_hash_registers_host_computed(tmp_path: Path) -> None:
    """download 路径与 cancelled 同语义：空 hash 登记 Host 流式计算值。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref(content_hash="")})

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH  # Host 计算值，不是空串
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}


def test_finish_completed_undeclared_empty_hash_registers_host_computed(
    tmp_path: Path,
) -> None:
    """未声明产物不落 job_dir，但 download 路径同样 digest 核验、登记计算值。"""
    storage = FakeStorage()
    extra_key = _staging_key("extra.json")
    storage.objects[STAGING_KEY] = PAYLOAD
    storage.objects[extra_key] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(
        handler,
        {"out.json": _remote_ref(), "extra.json": _remote_ref(key=extra_key, content_hash="")},
    )

    assert leases.results[0].status == "completed"
    assert not (job_dir / "extra.json").exists()
    row = object_store.lookup("job-1", "extra.json")
    assert row is not None
    assert row["content_hash"] == HASH
    assert storage.objects == {
        AUTHORITY_KEY: PAYLOAD,
        "jobs/ws-1/job-1/extra.json": PAYLOAD,
    }


class _FlakyCopyStorage(FakeStorage):
    """copy_object 在从指定 source key 拷贝时抛错（模拟中途 copy 失败）。"""

    def __init__(self, fail_source: str) -> None:
        super().__init__()
        self._fail_source = fail_source

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if source_key == self._fail_source:
            raise RuntimeError("copy boom")
        super().copy_object(source_key, destination_key)


def test_promote_mid_batch_copy_failure_rolls_back_authority_keys(tmp_path: Path) -> None:
    """rerun 覆盖式 promote 中途 copy 失败：已覆盖的 authority key 从备份
    恢复（旧清单行仍指向旧字节），备份 key 清理，无半应用状态。"""
    old_a, old_out = b"old-a-bytes", b"old-out-bytes"
    first_key = _staging_key("a.json")
    storage = _FlakyCopyStorage(fail_source=STAGING_KEY)  # out.json 的提升 copy 失败
    storage.objects[first_key] = PAYLOAD
    storage.objects[STAGING_KEY] = PAYLOAD
    storage.objects["jobs/ws-1/job-1/a.json"] = old_a  # rerun 前的旧 authority 对象
    storage.objects[AUTHORITY_KEY] = old_out
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    artifacts = {
        "a.json": _remote_ref(key=first_key),
        "out.json": _remote_ref(),
    }

    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": ["a.json", "out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts=artifacts),
        archive_name="",
    )

    assert leases.results[0].status == "failed"
    assert storage.objects["jobs/ws-1/job-1/a.json"] == old_a  # 已覆盖的被回滚
    assert storage.objects[AUTHORITY_KEY] == old_out  # 未轮到覆盖的保持旧字节
    assert not any("/.rollback/" in key for key in storage.objects)  # 备份清理
    assert object_store.lookup("job-1", "a.json") is None  # 未登记半应用清单行
    assert not (job_dir / "a.json").exists()
    assert not (job_dir / "out.json").exists()


def test_promote_rerun_success_cleans_up_backups(tmp_path: Path) -> None:
    """成功路径：旧 authority 对象被新字节覆盖，备份 key 同样清理。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    storage.objects[AUTHORITY_KEY] = b"stale-authority-bytes"
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}  # 无残留备份/staging
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH


def test_record_remote_many_rolls_back_on_mid_batch_failure(tmp_path: Path) -> None:
    """批量登记单事务：第二行写入失败（FK）时整批回滚，无部分行。"""
    storage = FakeStorage()
    other_key = "jobs/ws-1/job-2/other.json"
    storage.objects[AUTHORITY_KEY] = PAYLOAD
    storage.objects[other_key] = PAYLOAD
    _, _, _, object_store, _ = _make_handler(tmp_path, storage)
    rows = [
        {
            "workspace_id": "ws-1",
            "job_id": "job-1",
            "node_key": "node_a",
            "name": "out.json",
            "storage_key": AUTHORITY_KEY,
            "size_bytes": len(PAYLOAD),
            "content_hash": HASH,
        },
        {
            "workspace_id": "ws-1",
            # job-2 不存在：insert 触发 FK 违例，验证整批回滚。
            "job_id": "job-2",
            "node_key": "node_a",
            "name": "other.json",
            "storage_key": other_key,
            "size_bytes": len(PAYLOAD),
            "content_hash": HASH,
        },
    ]

    with pytest.raises(IntegrityError):
        object_store.record_remote_many(rows)

    assert object_store.lookup("job-1", "out.json") is None


# --- #338：gzip 双形态 ------------------------------------------------------

GZ_STAGING_KEY = STAGING_KEY + ".gz"
GZ_AUTHORITY_KEY = AUTHORITY_KEY + ".gz"
GZ_PAYLOAD = gzip.compress(PAYLOAD)


def _gz_ref(content_hash: str = HASH) -> dict:
    """v4 worker 上报形态：storage_key 带 .gz、size_bytes 是压缩后字节数、
    content_hash 是未压缩字节哈希。"""
    return {
        "storage_key": GZ_STAGING_KEY,
        "size_bytes": len(GZ_PAYLOAD),
        "content_hash": content_hash,
    }


def test_finish_gzip_ref_promotes_decoded_and_registers(tmp_path: Path) -> None:
    """.gz staging ref：HEAD 按压缩字节数核验，job_dir 落未压缩字节，
    authority key 带 .gz 后缀，清单行 hash=未压缩、size=压缩。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _gz_ref()})

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD  # 解压落盘
    assert storage.objects == {GZ_AUTHORITY_KEY: GZ_PAYLOAD}  # 提升保形态
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["storage_key"] == GZ_AUTHORITY_KEY
    assert row["content_hash"] == HASH
    assert row["size_bytes"] == len(GZ_PAYLOAD)


def test_finish_gzip_ref_hash_mismatch_fails(tmp_path: Path) -> None:
    """.gz 对象解压后字节对不上自报 hash：整批失败、无提升无落盘。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _gz_ref(content_hash="0" * 64)})

    result = leases.results[0]
    assert result.status == "failed"
    assert "hash mismatch" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert GZ_AUTHORITY_KEY not in storage.objects
    assert object_store.lookup("job-1", "out.json") is None


def test_finish_gzip_ref_compressed_size_mismatch_fails_head(tmp_path: Path) -> None:
    """HEAD 核验按压缩对象字节数：worker 报未压缩 size 会被拒。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, _ = _make_handler(tmp_path, storage)
    ref = {**_gz_ref(), "size_bytes": len(PAYLOAD)}  # 未压缩字节数 ≠ HEAD

    _finish(handler, {"out.json": ref})

    result = leases.results[0]
    assert result.status == "failed"
    assert "size" in result.error_message
    assert object_store.lookup("job-1", "out.json") is None


def test_finish_gzip_cancelled_empty_hash_registers_host_computed(tmp_path: Path) -> None:
    """cancelled 路径：.gz staging 字节边解压边 digest，登记 Host 计算值。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _gz_ref(content_hash="")}, status="cancelled")

    assert leases.results[0].status == "cancelled"
    assert not (job_dir / "out.json").exists()
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH
    assert storage.objects == {GZ_AUTHORITY_KEY: GZ_PAYLOAD}


def test_finish_rerun_form_change_raw_to_gzip(tmp_path: Path) -> None:
    """形态切换的重跑：上次裸对象（旧 worker）、这次 .gz（v4 worker）。
    新 authority key 带后缀、旧裸对象留存（无覆盖即无需备份），清单行
    单事务 retarget 到新 key；单节点重跑在新旧混合数据下通过。"""
    storage = FakeStorage()
    storage.objects[AUTHORITY_KEY] = b"previous-raw-bytes"
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    # 上一次运行的存量清单行（裸形态）。
    object_store.record_remote(
        workspace_id="ws-1",
        job_id="job-1",
        node_key="node_a",
        name="out.json",
        storage_key=AUTHORITY_KEY,
        size_bytes=len(b"previous-raw-bytes"),
        content_hash=hashlib.sha256(b"previous-raw-bytes").hexdigest(),
    )

    _finish(handler, {"out.json": _gz_ref()})

    assert leases.results[0].status == "completed"
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    # 旧裸对象未被覆盖（新 key 不存在即无备份/回滚），新对象带后缀。
    assert storage.objects == {AUTHORITY_KEY: b"previous-raw-bytes", GZ_AUTHORITY_KEY: GZ_PAYLOAD}
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["storage_key"] == GZ_AUTHORITY_KEY
    assert row["content_hash"] == HASH


def test_verify_remote_accepts_both_staging_key_forms(tmp_path: Path) -> None:
    """host 按后缀判定两种上传形态都收：裸 key 与 .gz key 均通过布局核验，
    错位的 key（别的 execution / authority key）照旧拒绝。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    _, _, _, object_store, _ = _make_handler(tmp_path, storage)

    for key, size in ((STAGING_KEY, len(PAYLOAD)), (GZ_STAGING_KEY, len(GZ_PAYLOAD))):
        object_store.verify_remote(
            workspace_id="ws-1",
            job_id="job-1",
            name="out.json",
            storage_key=key,
            size_bytes=size,
            execution_id="exec-1",
        )

    with pytest.raises(ValueError, match="unexpected artifact storage key"):
        object_store.verify_remote(
            workspace_id="ws-1",
            job_id="job-1",
            name="out.json",
            storage_key="jobs-staging/ws-1/job-1/other-exec/out.json.gz",
            size_bytes=len(GZ_PAYLOAD),
            execution_id="exec-1",
        )


# --- #338 评审 r1 P2-2：decompression-bomb 缺口 ------------------------------


def test_finish_gzip_ref_decompression_bomb_fails(tmp_path: Path) -> None:
    """r1 P2-2 回归：压缩字节过了 max_archive_bytes 闸但解压后超限——下载
    中途计数中断，整批判 failed，炸弹不落 job_dir、不提升、不登记。"""
    bomb_raw = b"x" * 4096
    bomb_gz = gzip.compress(bomb_raw)
    assert len(bomb_gz) < len(bomb_raw)  # 测试前提：压缩确实更小
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = bomb_gz
    # 闸值夹在压缩/未压缩之间：verify_remote 按压缩字节过，解压路径必须拦。
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, max_archive_bytes=len(bomb_gz)
    )
    ref = {
        "storage_key": GZ_STAGING_KEY,
        "size_bytes": len(bomb_gz),
        "content_hash": hashlib.sha256(bomb_raw).hexdigest(),
    }

    _finish(handler, {"out.json": ref})

    result = leases.results[0]
    assert result.status == "failed"
    assert "decompresses beyond the size limit" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert GZ_AUTHORITY_KEY not in storage.objects
    assert object_store.lookup("job-1", "out.json") is None


def test_finish_gzip_cancelled_bomb_fails_on_digest_path(tmp_path: Path) -> None:
    """cancelled 路径（verify_remote_digest，不落盘）同样按解压字节计数中断。"""
    bomb_gz = gzip.compress(b"x" * 4096)
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = bomb_gz
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, max_archive_bytes=len(bomb_gz)
    )
    ref = {"storage_key": GZ_STAGING_KEY, "size_bytes": len(bomb_gz), "content_hash": ""}

    _finish(handler, {"out.json": ref}, status="cancelled")

    result = leases.results[0]
    assert result.status == "failed"
    assert "decompresses beyond the size limit" in result.error_message
    assert not (job_dir / "out.json").exists()
    assert object_store.lookup("job-1", "out.json") is None
