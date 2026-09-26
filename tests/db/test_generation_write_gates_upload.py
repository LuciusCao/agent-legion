"""EXEC-GENERATION-001 产物上传写闸与 P1-B staging/promote 回滚（#645 P2-b，
#759 复审 P1-B/P1-2）。

自 test_generation_write_gates.py 拆出（#779 codex 列车复审 P1-4——文件
超 800 拆分线，按写面拆成 upload/fanout/finish 三姊妹文件，用例零改动
迁移）。共享种子/同步工具见 tests/db/generation_write_gate_helpers.py。

P2-b（本地 code 孤儿执行的迟来上传）：心跳丢失后沙箱子进程是协作式取消，
可跑完再进 ``_check_outputs`` → ``upload_produced_artifacts``。写闸与
broker ownership 同源（lease 行 active + 落戳代次 == jobs 现值；心跳饥
饿的延迟清扫由 HeartbeatDeferral 语义覆盖，不按 expires_at 单独判死，
codex #774 P1）不过则整批不上传。
P1-B 起 lease 臂上传改走 staging：字节先落 per-lease staging key，再经共享
primitive（``executors._artifact_promotion.promote_to_authority_guarded``）
备份 → copy → 锁内复查 + 登记 → 闸拒按回滚备份恢复 authority——整个
按 key 序列经 ``artifact-authority:<key>`` advisory 锁串行（同一事务内，
commit 时刻失败除外），「入口闸通过后、登记前 reset 提交」的窗口既不复活
已删清单行，也不让保留的旧行指向被污染的字节。

#759 复审 P1-2（登记异常路径）：锁内登记抛异常时 promote 按回滚备份恢复
authority，且已落盘的新文件经 FilePromotionGuard 整体回滚——旧清单行永远
指向匹配的旧字节，不留半应用 job_dir。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from server.app.db.transaction import write_transaction
from server.app.executors import _artifact_promotion
from server.app.executors._lease_control import lock_job_mutation_and_read_generation
from server.app.executors.artifact_mirror import upload_produced_artifacts
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.db.generation_write_gate_helpers import (
    TIMED_DATABASE_URL,
    _assert_no_staging_residue,
    _await_job_mutation_waiter,
    _join,
    _ResetAfterStagingPutStorage,
    _seed_authority_object,
    _seed_job,
    _seed_lease,
    _start,
    _store,
)
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL


def test_upload_writes_when_lease_owns_current_generation(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """对照组：lease active + 代次一致 → 上传与清单行登记照常。"""
    _seed_job(job_db, workspace_id="gate1-ws", job_id="gate1-job")
    _seed_lease(job_db, workspace_id="gate1-ws", job_id="gate1-job")
    (tmp_path / "out.json").write_bytes(b'{"ok": true}')
    store = _store()

    upload_produced_artifacts(
        store,
        workspace_id="gate1-ws",
        job_id="gate1-job",
        node_key="node_a",
        job_dir=tmp_path,
        produced=("out.json",),
        lease_id="lease-1",
    )

    storage = store.storage
    assert isinstance(storage, FakeObjectStorage)
    assert storage.objects == {"jobs/gate1-ws/gate1-job/out.json": b'{"ok": true}'}
    row = store.row_for_node("gate1-job", "node_a", "out.json")
    assert row is not None
    assert row["storage_key"] == "jobs/gate1-ws/gate1-job/out.json"


def test_upload_writes_when_lease_expired_but_still_active(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """codex #774 P1（写闸与 broker ownership 同源）：expires_at 已过但
    status 仍 active 的 lease——HeartbeatDeferral 刻意保留的延迟清扫窗
    （Worker 控制面新鲜、心跳静默 < 2×TTL）——写闸必须放行：闸若按
    expires_at 单独判死，会把仍被 finish_lease 承认的结果的字节面判死，
    成功节点被永久翻成失败。ownership 的唯一撤销通道是 lease 行的删除/
    状态翻转（sweeper/expiry/finish 持 job-mutation 锁），不是时间戳。"""
    _seed_job(job_db, workspace_id="gate1b-ws", job_id="gate1b-job")
    _seed_lease(job_db, workspace_id="gate1b-ws", job_id="gate1b-job")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update executor_leases"
            " set expires_at=current_timestamp - interval '5 minutes',"
            " heartbeat_at=current_timestamp - interval '5 minutes'"
            " where id='lease-1'"
        )
    (tmp_path / "out.json").write_bytes(b'{"deferred": true}')
    store = _store()

    upload_produced_artifacts(
        store,
        workspace_id="gate1b-ws",
        job_id="gate1b-job",
        node_key="node_a",
        job_dir=tmp_path,
        produced=("out.json",),
        lease_id="lease-1",
    )

    storage = store.storage
    assert isinstance(storage, FakeObjectStorage)
    assert storage.objects == {"jobs/gate1b-ws/gate1b-job/out.json": b'{"deferred": true}'}
    row = store.row_for_node("gate1b-job", "node_a", "out.json")
    assert row is not None
    assert row["storage_key"] == "jobs/gate1b-ws/gate1b-job/out.json"


def test_upload_skipped_when_lease_expired(job_db: JobQueries, tmp_path: Path) -> None:
    """孤儿路径：lease 已被 sweeper 置 expired → 整批不上传、不登记。"""
    _seed_job(job_db, workspace_id="gate2-ws", job_id="gate2-job")
    _seed_lease(job_db, workspace_id="gate2-ws", job_id="gate2-job")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("update executor_leases set status='expired' where id='lease-1'")
    (tmp_path / "out.json").write_bytes(b"old-bytes")
    store = _store()

    upload_produced_artifacts(
        store,
        workspace_id="gate2-ws",
        job_id="gate2-job",
        node_key="node_a",
        job_dir=tmp_path,
        produced=("out.json",),
        lease_id="lease-1",
    )

    storage = store.storage
    assert isinstance(storage, FakeObjectStorage)
    assert storage.put_calls == 0
    assert storage.objects == {}
    assert store.row_for_node("gate2-job", "node_a", "out.json") is None


def test_upload_skipped_when_generation_bumped(job_db: JobQueries, tmp_path: Path) -> None:
    """代次臂：lease 行仍 active 但 jobs 代次已被 reset bump → 不上传。"""
    _seed_job(job_db, workspace_id="gate3-ws", job_id="gate3-job")
    _seed_lease(job_db, workspace_id="gate3-ws", job_id="gate3-job")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set execution_generation=execution_generation+1 where id='gate3-job'"
        )
    (tmp_path / "out.json").write_bytes(b"old-bytes")
    store = _store()

    upload_produced_artifacts(
        store,
        workspace_id="gate3-ws",
        job_id="gate3-job",
        node_key="node_a",
        job_dir=tmp_path,
        produced=("out.json",),
        lease_id="lease-1",
    )

    storage = store.storage
    assert isinstance(storage, FakeObjectStorage)
    assert storage.put_calls == 0
    assert store.row_for_node("gate3-job", "node_a", "out.json") is None


def test_registration_gate_catches_reset_after_entry_check(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（P1-B，RMW/输入保护分支：reset 保留旧清单行）：入口闸通过、
    staging 字节落盘后 reset 提交 bump 代次——promote 的锁内复查拒绝登记，
    并按回滚备份恢复 authority：旧行的 content_hash/size_bytes 不变、
    authority 仍是旧字节、staging 与回滚残留都清理干净。
    （修复前语义——旧字节已被覆盖、旧行指向污染字节——由本用例的逐字节
    断言钉死为不再发生。）"""
    _seed_job(job_db, workspace_id="gate4-ws", job_id="gate4-job")
    _seed_lease(job_db, workspace_id="gate4-ws", job_id="gate4-job")
    storage = _ResetAfterStagingPutStorage(job_id="gate4-job", delete_row=False)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    old_row = _seed_authority_object(
        store, tmp_path, workspace_id="gate4-ws", job_id="gate4-job", payload=b"old-bytes"
    )
    # 入口闸（artifact_mirror 循环级预检）在 reset 之前通过。
    assert store.artifact_write_gate_open(job_id="gate4-job", lease_id="lease-1")
    storage.armed = True
    (tmp_path / "out.json").write_bytes(b"new-bytes-from-stale-epoch")

    result = store.upload(
        workspace_id="gate4-ws",
        job_id="gate4-job",
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "out.json",
        lease_id="lease-1",
    )

    assert result is None  # 锁内复查拒绝登记
    row = store.row_for_node("gate4-job", "node_a", "out.json")
    assert row is not None  # RMW 保留的旧行不被复活式覆盖
    assert row["content_hash"] == old_row["content_hash"]
    assert row["size_bytes"] == old_row["size_bytes"]
    authority_key = "jobs/gate4-ws/gate4-job/out.json"
    assert storage.objects[authority_key] == b"old-bytes"  # 回滚备份恢复，未被污染
    assert storage.put_calls == 2  # 种子直写 + staging put；authority 只经 copy
    _assert_no_staging_residue(storage)


def test_registration_gate_catches_reset_that_removed_row(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（P1-B，clean 重置分支：reset 删除旧清单行）：同一窗口下闸拒
    后已删行不复活；authority 对象同样按备份恢复旧字节（此时旧对象已成
    孤儿，lifecycle 兜底，但字节绝不被旧代次污染）。"""
    _seed_job(job_db, workspace_id="gate4b-ws", job_id="gate4b-job")
    _seed_lease(job_db, workspace_id="gate4b-ws", job_id="gate4b-job")
    storage = _ResetAfterStagingPutStorage(job_id="gate4b-job", delete_row=True)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    _seed_authority_object(
        store, tmp_path, workspace_id="gate4b-ws", job_id="gate4b-job", payload=b"old-bytes"
    )
    assert store.artifact_write_gate_open(job_id="gate4b-job", lease_id="lease-1")
    storage.armed = True
    (tmp_path / "out.json").write_bytes(b"new-bytes-from-stale-epoch")

    result = store.upload(
        workspace_id="gate4b-ws",
        job_id="gate4b-job",
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "out.json",
        lease_id="lease-1",
    )

    assert result is None
    assert store.row_for_node("gate4b-job", "node_a", "out.json") is None  # 已删行不复活
    authority_key = "jobs/gate4b-ws/gate4b-job/out.json"
    assert storage.objects[authority_key] == b"old-bytes"  # 备份恢复
    _assert_no_staging_residue(storage)


def test_upload_registration_waits_on_mutation_lock_then_rolls_back(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """锁序交错（pg_locks 观测）：mutation 持 job-mutation 锁、未提交 bump 时，
    upload 的登记事务必须先等锁（证明锁内复查与 mutation 互斥）；mutation
    提交（代次 → 1、旧行保留）后闸拒并恢复 authority。最终状态 == 串行序
    「reset → 旧代次上传被拒」。

    突变自检：登记事务若不取 job-mutation 锁，B 不会在 pg_locks 里出现
    （同步点超时），且会在 mutation 提交前登记成功（result 非 None）。"""
    _seed_job(job_db, workspace_id="gate4c-ws", job_id="gate4c-job")
    _seed_lease(job_db, workspace_id="gate4c-ws", job_id="gate4c-job")
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(TIMED_DATABASE_URL, storage)
    old_row = _seed_authority_object(
        store, tmp_path, workspace_id="gate4c-ws", job_id="gate4c-job", payload=b"old-bytes"
    )
    assert store.artifact_write_gate_open(job_id="gate4c-job", lease_id="lease-1")
    (tmp_path / "out.json").write_bytes(b"new-bytes-from-stale-epoch")

    stack = contextlib.ExitStack()
    conn_a = stack.enter_context(write_transaction(TIMED_DATABASE_URL))
    assert lock_job_mutation_and_read_generation(conn_a, "gate4c-job") == 0
    conn_a.execute(
        "update jobs set execution_generation=execution_generation+1 where id='gate4c-job'"
    )

    def _late_upload() -> Any:
        return store.upload(
            workspace_id="gate4c-ws",
            job_id="gate4c-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "out.json",
            lease_id="lease-1",
        )

    thread, outcome = _start(_late_upload)
    _await_job_mutation_waiter("gate4c-job")  # B 的登记事务卡在 advisory 锁上
    stack.close()  # 提交 reset
    _join(thread)

    assert outcome.get("error") is None
    assert outcome.get("result") is None  # 闸拒
    row = store.row_for_node("gate4c-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == old_row["content_hash"]
    assert row["size_bytes"] == old_row["size_bytes"]
    authority_key = "jobs/gate4c-ws/gate4c-job/out.json"
    assert storage.objects[authority_key] == b"old-bytes"
    _assert_no_staging_residue(storage)


def test_registration_exception_restores_authority_and_rolls_back_files(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """upsert 在锁内文件提升之后抛异常：promote 必须把已覆盖的 authority
    按回滚备份恢复（旧清单行继续指向匹配的旧字节）、把已落盘的新文件整体
    回滚（旧文件归位、无半应用现场），清单事务回滚不留新行，异常原样上抛。"""
    _seed_job(job_db, workspace_id="gate7-ws", job_id="gate7-job")
    _seed_lease(job_db, workspace_id="gate7-ws", job_id="gate7-job")
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    old_row = _seed_authority_object(
        store, tmp_path, workspace_id="gate7-ws", job_id="gate7-job", payload=b"old-bytes"
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"old-local-bytes")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (staged_dir / "out.json").write_bytes(b"new-local-bytes")
    staging_key = "jobs/gate7-ws/gate7-job/staging/lease-1/out.json"
    authority_key = "jobs/gate7-ws/gate7-job/out.json"
    rollback_key = "jobs/gate7-ws/gate7-job/staging/lease-1/.rollback/out.json"
    storage.objects[staging_key] = b"new-bytes"

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected upsert failure")

    monkeypatch.setattr(_artifact_promotion, "upsert_artifact_row_tx", _boom)

    with pytest.raises(RuntimeError, match="injected upsert failure"):
        _artifact_promotion.promote_to_authority_guarded(
            storage,
            TEST_DATABASE_URL,
            job_id="gate7-job",
            lease_id="lease-1",
            copies=[
                _artifact_promotion.AuthorityCopy(
                    name="out.json",
                    staging_key=staging_key,
                    authority_key=authority_key,
                    rollback_key=rollback_key,
                )
            ],
            rows=[
                {
                    "job_id": "gate7-job",
                    "node_key": "node_a",
                    "name": "out.json",
                    "storage_key": authority_key,
                    "size_bytes": 9,
                    "content_hash": "injected",
                }
            ],
            staged_files={"out.json": staged_dir / "out.json"},
            job_dir=job_dir,
        )

    assert storage.objects[authority_key] == b"old-bytes"  # 回滚备份恢复
    row = store.row_for_node("gate7-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == old_row["content_hash"]  # 清单行未被部分登记
    assert (job_dir / "out.json").read_bytes() == b"old-local-bytes"  # 文件提升回滚
    assert not list(job_dir.glob(".promote-rollback-*"))  # 备份目录已清
    assert not any(".rollback" in key for key in storage.objects)  # 回滚对象已清


def test_file_moves_guarded_mid_batch_failure_rolls_back(tmp_path: Path) -> None:
    """多文件提升中途失败：已移动的第一个文件回滚归位，未留下第二个文件，
    备份目录清理——不留半应用的 job_dir。"""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "a.json").write_bytes(b"old-a")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (staged_dir / "a.json").write_bytes(b"new-a")
    moves = [
        (job_dir / "a.json", staged_dir / "a.json"),
        (job_dir / "b.json", staged_dir / "missing.json"),
    ]

    with pytest.raises(FileNotFoundError):
        _artifact_promotion.promote_file_moves_guarded(moves, backup_parent=job_dir)

    assert (job_dir / "a.json").read_bytes() == b"old-a"
    assert not (job_dir / "b.json").exists()
    assert not list(job_dir.glob(".promote-rollback-*"))
