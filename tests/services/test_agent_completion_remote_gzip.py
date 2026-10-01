"""Worker 直传 S3 产物引用的 gzip 双形态与 cancelled 路径 digest 核验。

Split from ``test_agent_completion_remote.py`` for the test-file line budget
(#207): the base verify-then-apply surface stays in the main file; this
sibling owns the #338 dual-form (.gz staging ref) and the #356 spot-check /
decompression-bomb surface. The ``_make_handler`` fixture stack and the small
ref helpers are duplicated per sibling (repo convention for split suites).

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

    def finish(self, lease_id: str, result: Any, *, stage_timer: Any = None) -> bool:
        # stage_timer: the #521 result-stage split threads an optional timer
        # through finish; the stub only records the result.
        _ = stage_timer
        self.results.append(result)
        return True


class _StubArtifactStore:
    def __init__(self) -> None:
        self.refs: list[tuple[str, str, str, str]] = []

    def add_ref(self, job_id: str, node_key: str, name: str, digest: str) -> None:
        self.refs.append((job_id, node_key, name, digest))


def _remote_ref(key: str = STAGING_KEY, size: int | None = None, content_hash: str = HASH) -> dict:
    return {
        "storage_key": key,
        "size_bytes": len(PAYLOAD) if size is None else size,
        "content_hash": content_hash,
    }


def _make_handler(
    tmp_path: Path,
    storage: FakeStorage | None,
    max_archive_bytes: int | None = None,
    spot_check_percent: int | None = None,
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
        # #645 P2-a：promote 的代次写闸要求活跃 lease（active + 心跳新鲜 +
        # 落戳代次 == jobs 现值，默认双双为 0）。
        cursor = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values ('job-1', 'node_a', 'running', '[]', '', '', '', current_timestamp)"
            " returning id"
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values ('lease-1', 'exec-1', 'agent:worker-1', 'ws-1', 'job-1', 'node_a', %s,"
            " 'active', current_timestamp, current_timestamp,"
            " current_timestamp + interval '1 hour')",
            (cursor.fetchone()["id"],),
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
        spot_check_percent=spot_check_percent,
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
    # 提升保形态；staging 源在 finish 提交后由完成方删除。
    assert storage.objects == {GZ_AUTHORITY_KEY: GZ_PAYLOAD}
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
    assert storage.objects == {GZ_AUTHORITY_KEY: GZ_PAYLOAD}  # staging 在 finish 后删除


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
    # 旧裸对象未被覆盖（新 key 不存在即无备份/回滚），新对象带后缀；
    # staging 源在 finish 提交后删除。
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


def test_finish_cancelled_unsampled_trusts_reported_hash(tmp_path: Path) -> None:
    """#356 plan B：cancelled 路径、自报 hash 且未入抽检样本 → 不下载字节，
    登记自报值（kill-switch 0 = 全信任）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, spot_check_percent=0
    )

    _finish(handler, {"out.json": _remote_ref()}, status="cancelled")

    assert leases.results[0].status == "cancelled"
    assert not (job_dir / "out.json").exists()
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH  # 自报值被登记
    assert storage.opened == 0  # 未打开对象流——第二跳流量消除


def test_finish_cancelled_empty_hash_always_streams(tmp_path: Path) -> None:
    """#356：自报 hash 为空时无条件流式计算（无可信任值，manifest 行需要
    Host 计算 digest）——即便 percent=0。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, spot_check_percent=0
    )

    _finish(handler, {"out.json": _remote_ref(content_hash="")}, status="cancelled")

    assert leases.results[0].status == "cancelled"
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH  # Host 计算值
    assert storage.opened == 1


def test_finish_cancelled_sampled_mismatch_still_fails(tmp_path: Path) -> None:
    """#356：抽中的样本照旧全量核验（percent=100 与专项 mismatch 测试互为
    补充：本测试确认样本臂的失败语义未被信任路径吞掉）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, _ = _make_handler(tmp_path, storage, spot_check_percent=100)

    _finish(handler, {"out.json": _remote_ref(content_hash="0" * 64)}, status="cancelled")

    assert leases.results[0].status == "failed"
    assert "hash mismatch" in leases.results[0].error_message


def test_finish_cancelled_unsampled_gzip_still_streams_for_the_cap(tmp_path: Path) -> None:
    """#356 review P1：未抽样的 .gz 引用不得走信任捷径——HEAD 只约束压缩
    字节，read_bounded 的解压上限是流本身的安全属性。percent=0（全信任）
    也必须对 gzip 流式核验，防未抽样的解压炸弹被登记。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, spot_check_percent=0
    )

    _finish(handler, {"out.json": _gz_ref()}, status="cancelled")

    # 未抽样也打开了对象流（解压上限生效），登记自报 hash。
    assert storage.opened == 1
    assert leases.results[0].status == "cancelled"
    row = object_store.lookup("job-1", "out.json")
    assert row is not None
    assert row["content_hash"] == HASH


def test_finish_cancelled_unsampled_gzip_bomb_fails(tmp_path: Path) -> None:
    """解压后超限（max_archive_bytes 远小于解压结果）：即使未抽样、自报
    hash 匹配，read_bounded 也拒绝——信任捷径不得绕过炸弹防护。"""
    decompressed = b"x" * (4 * 1024 * 1024)
    big = gzip.compress(decompressed)  # 压缩后很小
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = big
    # 自报 hash（未压缩哈希）与 size（压缩后字节数）都如实——HEAD 两项
    # 核验全过，唯一防线是 read_bounded 的解压上限。
    import hashlib as _hashlib

    honest = _hashlib.sha256(decompressed).hexdigest()
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, max_archive_bytes=1024, spot_check_percent=0
    )
    ref = _gz_ref(content_hash=honest)
    ref["size_bytes"] = len(big)

    _finish(handler, {"out.json": ref}, status="cancelled")

    result = leases.results[0]
    assert result.status == "failed"
    assert "exceeds" in result.error_message
    assert object_store.lookup("job-1", "out.json") is None
    assert GZ_AUTHORITY_KEY not in storage.objects


def test_finish_cancelled_unsampled_gzip_lying_hash_fails(tmp_path: Path) -> None:
    """#356 review：percent=0（全信任）下撒谎的 gzip 自报 hash 仍必失败
    ——区分「gzip 永远全量核验」与「裸键信任捷径」：同场景的裸键会
    静默通过，gzip 不许。"""
    storage = FakeStorage()
    storage.objects[GZ_STAGING_KEY] = GZ_PAYLOAD
    handler, leases, _, _, _ = _make_handler(tmp_path, storage, spot_check_percent=0)

    _finish(handler, {"out.json": _gz_ref(content_hash="0" * 64)}, status="cancelled")

    result = leases.results[0]
    assert result.status == "failed"
    assert "hash mismatch" in result.error_message
