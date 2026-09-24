"""AgentCompletionHandler 接收 Worker 直传 S3 的产物引用（#160 D12）。

Worker 直传到每次 execution 唯一的 staging key（jobs-staging/...）；Host
先全部核验（staging 布局绑定本 execution、HEAD size、下载 hash），再统一
服务端 copy 提升到权威 key + 原子落盘（只落 expected_outputs 白名单）+
record_remote 登记 + best-effort 删 staging；任一失败整个 result 判
failed，且不留半应用状态。旧形态 str ref 的 add_ref 路径不变（回归由
tests/services/test_agent_completion_validation.py 覆盖）。

姊妹文件：#338 gzip 双形态与 #356 抽检/解压炸弹面在
test_agent_completion_remote_gzip.py；#645 P2-a 的 promote 代次写闸用例
在本文件末尾。
"""

from __future__ import annotations

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


def _staging_key(name: str, execution_id: str = "exec-1") -> str:
    return f"jobs-staging/ws-1/job-1/{execution_id}/{name}"


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
        # #645 P2-a：promote 的代次写闸要求活跃 lease（行存在 + active +
        # 落戳代次 == jobs 现值，默认双双为 0；codex #774 P1 起不按
        # expires_at 单独判死——与 finish_lease/broker 清扫同谓词）。
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


def test_finish_remote_ref_promotes_downloads_and_registers(tmp_path: Path) -> None:
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    assert leases.results[0].status == "completed"
    assert leases.results[0].produced_artifacts == ("out.json",)
    assert (job_dir / "out.json").read_bytes() == PAYLOAD
    # 服务端 copy 提升到权威 key；staging 对象在 finish 提交后由完成方
    # 删除（promote→finish 窗口内绝不删，#774 对抗复审 P1）。
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
    assert storage.objects == {AUTHORITY_KEY: PAYLOAD}  # staging 在 finish 提交后删除


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
    """cancelled 路径被抽中时同样 digest 核验 staging 字节：自报 hash 不符
    整批失败（percent=100 钉住「必抽中」分支；未抽中分支见下方专项测试）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(
        tmp_path, storage, spot_check_percent=100
    )

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
    }  # 两个 staging 源都在 finish 提交后删除


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
    """成功路径：旧 authority 对象被新字节覆盖，per-invocation 备份 key 与
    staging 源都在 finish 提交后清理干净。"""
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


# --- #645 P2-a：promote 的代次写闸（EXEC-GENERATION-001 产物字节面） ----------


def _tear_down_lease_and_bump_generation(*, keep_lease: bool = False) -> None:
    """模拟 commit 前置校验通过后、sweep 删 lease + reset/upgrade bump 代次
    已提交的终态（``keep_lease=True`` 保留 lease 行，只 bump 代次——隔离
    代次 CAS 臂与 lease 存在臂）。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        if not keep_lease:
            conn.execute("delete from executor_leases where id='lease-1'")
        conn.execute("update jobs set execution_generation=execution_generation+1 where id='job-1'")


def test_promote_rejected_when_lease_gone_before_apply(tmp_path: Path) -> None:
    """前置闸：promote 开始前 lease 已被 sweep 删除、代次已 bump → 不 copy、
    不落盘、不登记；结果按「lease 不再活跃」失败（commit 侧即既有 409 语义）。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    _tear_down_lease_and_bump_generation()

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "no longer active" in result.error_message
    assert storage.objects == {STAGING_KEY: PAYLOAD}  # 无 copy、无删除
    assert object_store.lookup("job-1", "out.json") is None
    assert not (job_dir / "out.json").exists()


class _SweepOnAuthorityCopyStorage(FakeStorage):
    """copy 到权威 key 的同一瞬间提交 sweep+reset（promote 中途交错窗口）。"""

    def __init__(self, *, keep_lease: bool = False) -> None:
        super().__init__()
        self._keep_lease = keep_lease

    def copy_object(self, source_key: str, destination_key: str) -> None:
        super().copy_object(source_key, destination_key)
        if destination_key == AUTHORITY_KEY:
            _tear_down_lease_and_bump_generation(keep_lease=self._keep_lease)


def test_promote_rejected_when_reset_commits_mid_promote(tmp_path: Path) -> None:
    """交错用例（#645 P2-a）：前置闸之后、行登记之前 sweep+reset 提交——
    已落地的 authority copy 必须从备份恢复、清单行不得复活、job_dir 不写
    入；staging 留存（与失败路径同语义，lifecycle 兜底）。"""
    old_bytes = b"previous-authority-bytes"
    storage = _SweepOnAuthorityCopyStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    storage.objects[AUTHORITY_KEY] = old_bytes  # 重置前已登记的权威字节
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "no longer active" in result.error_message
    assert storage.objects[AUTHORITY_KEY] == old_bytes  # 覆盖被备份恢复
    assert not any("/.rollback/" in key for key in storage.objects)  # 备份清理
    assert object_store.lookup("job-1", "out.json") is None  # 清单行未复活
    assert not (job_dir / "out.json").exists()


def test_promote_rejected_on_generation_mismatch_with_live_lease(tmp_path: Path) -> None:
    """代次臂独立于 lease 存在臂：reset bump 了代次但 lease 行仍 active
    （如并发 upgrade 未等心跳过期）→ 代次不符照样拒绝，零字节零行。"""
    storage = FakeStorage()
    storage.objects[STAGING_KEY] = PAYLOAD
    handler, leases, _, object_store, job_dir = _make_handler(tmp_path, storage)
    _tear_down_lease_and_bump_generation(keep_lease=True)

    _finish(handler, {"out.json": _remote_ref()})

    result = leases.results[0]
    assert result.status == "failed"
    assert "no longer active" in result.error_message
    assert storage.objects == {STAGING_KEY: PAYLOAD}
    assert object_store.lookup("job-1", "out.json") is None
    assert not (job_dir / "out.json").exists()


def test_finish_remote_ref_invalid_name_fails(tmp_path: Path) -> None:
    """#759 对抗复审 P2：Worker 自选的 dict-ref 名是不可信输入——含路径
    分隔符的名（如 .rollback/out.json）会与 promote 的回滚 key 命名空间
    碰撞（备份 copy 覆盖 Worker 自己的 staging 对象，清单行指向 hash 不
    匹配的字节）。按本地上传同一把尺（valid_artifact_name）拒收，整个
    结果翻 failed，零 copy、零登记。"""
    storage = FakeStorage()
    handler, leases, _artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    _finish(
        handler,
        {
            ".rollback/out.json": _remote_ref(
                key="jobs-staging/ws-1/job-1/exec-1/.rollback/out.json"
            )
        },
    )

    result = leases.results[0]
    assert result.status == "failed"
    assert "invalid artifact name" in result.error_message
    assert storage.objects == {}
    assert object_store.lookup("job-1", ".rollback/out.json") is None
    assert not (job_dir / ".rollback").exists()


def test_finish_remote_ref_nested_name_accepted(tmp_path: Path) -> None:
    """对照（#631 祝福的嵌套声明名）：reports/final.json 这类合法嵌套名
    不受非法名拒收影响，全链路照常 promote + 登记。"""
    nested = "reports/final.json"
    staging_key = "jobs-staging/ws-1/job-1/exec-1/reports/final.json"
    storage = FakeStorage()
    storage.objects[staging_key] = PAYLOAD
    handler, leases, _artifact_store, object_store, job_dir = _make_handler(tmp_path, storage)

    handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="job-1",
        node_key="node_a",
        manifest={"expected_outputs": [nested], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={nested: _remote_ref(key=staging_key)},
        ),
        archive_name="",
    )

    assert leases.results[0].status == "completed"
    assert (job_dir / "reports" / "final.json").read_bytes() == PAYLOAD
    row = object_store.lookup("job-1", nested)
    assert row is not None
