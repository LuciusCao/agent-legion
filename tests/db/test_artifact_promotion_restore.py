"""promote 恢复臂的失败语义（codex #774 P1）：恢复失败时回滚备份必须留存。

钉住的不变量：**回滚备份的删除以其防范状态已确认解除为前提**——登记提
交（新字节权威化）、恢复成功（旧字节回位）或该 key 从未被覆盖（备份本
就冗余）。恢复 copy 经有界重试吸收瞬时存储故障；重试耗尽仍失败时备份
是幸存清单行所指向旧字节的最后恢复源，必须保留，ERROR 日志携带
authority/backup key 作为恢复指针。三条恢复臂（锁内 copy/登记失败、锁
内闸拒、commit 时刻连接死亡）逐臂钉住。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

import server.app.executors._artifact_promotion as ap
from server.app.db.transaction import write_transaction
from server.app.executors._artifact_promotion import (
    AuthorityCopy,
    promote_to_authority_guarded,
)
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore, artifact_storage_key
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复重试的退避 sleep 打桩——持久失败用例的真实等待（约 5s）纯为时
    序服务，与断言语义无关（#774 对抗复审 P3）。本文件用例均单线程。"""
    monkeypatch.setattr(ap.time, "sleep", lambda _seconds: None)


def _seed_job(job_db: JobQueries, *, workspace_id: str, job_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'ws', 'demo_workflow')"
            " on conflict (id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id) values (%s, %s, 's', 's1')",
            (job_id, workspace_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'node_a')", (job_id,))


def _seed_lease(
    job_db: JobQueries, *, workspace_id: str, job_id: str, lease_id: str, generation: int
) -> None:
    with job_db.connect() as conn:
        cursor = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values (%s, 'node_a', 'running', '[]', '', '', '', current_timestamp) returning id",
            (job_id,),
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at,"
            " execution_generation)"
            " values (%s, %s, 'code', %s, %s, 'node_a', %s, 'active', current_timestamp,"
            " current_timestamp, current_timestamp + interval '1 hour', %s)",
            (
                lease_id,
                f"exec-{lease_id}",
                workspace_id,
                job_id,
                cursor.fetchone()["id"],
                generation,
            ),
        )


class _FlakyRestoreStorage(FakeObjectStorage):
    """恢复 copy（rollback→authority）的前 ``failures`` 次必抛瞬时存储故障。"""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self._remaining = failures
        self.restore_attempts = 0

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if "/.rollback/" in source_key:
            self.restore_attempts += 1
            if self._remaining > 0:
                self._remaining -= 1
                raise ConnectionError("s3 transient")
        super().copy_object(source_key, destination_key)


def _seed_committed_artifact(
    job_db: JobQueries,
    tmp_path: Path,
    store: JobArtifactObjectStore,
    *,
    workspace_id: str,
    job_id: str,
) -> None:
    """落一份已提交的旧字节产物（authority 对象 + 清单行同源）。"""
    _seed_job(job_db, workspace_id=workspace_id, job_id=job_id)
    _seed_lease(job_db, workspace_id=workspace_id, job_id=job_id, lease_id="lease-0", generation=0)
    (tmp_path / "old.json").write_bytes(b"old-bytes")
    assert (
        store.upload(
            workspace_id=workspace_id,
            job_id=job_id,
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "old.json",
        )
        is not None
    )


def _bump_generation(job_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("update jobs set execution_generation=1 where id=%s", (job_id,))


def test_transient_restore_failure_retries_and_cleans_backup(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """闸拒臂 + 瞬时恢复故障：有界重试在原地收敛——恢复成功后 authority
    回到旧字节、回滚备份照常清理（备份防范的状态已解除）。无重试时首次
    失败即落定：authority 停在错位的新字节（本测试 hash 断言变红）。"""
    storage = _FlakyRestoreStorage(failures=1)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    _seed_committed_artifact(job_db, tmp_path, store, workspace_id="rt-ws", job_id="rt-job")
    _bump_generation("rt-job")  # lease-0 落戳 0 代 → 闸拒
    (tmp_path / "new.json").write_bytes(b"new-bytes")

    result = store.upload(
        workspace_id="rt-ws",
        job_id="rt-job",
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "new.json",
        lease_id="lease-0",
    )

    assert result is None  # 过期 promote 闸拒
    assert storage.restore_attempts == 2  # 首次瞬时失败，重试收敛
    assert storage.objects["jobs/rt-ws/rt-job/out.json"] == b"old-bytes"
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]


def test_persistent_restore_failure_retains_rollback_backup(
    job_db: JobQueries, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """codex #774 P1 主案（闸拒臂）：恢复重试耗尽仍失败时回滚备份必须保留
    ——它是幸存清单行仍指向的旧字节的最后恢复源（authority 上的错位新字
    节与备份一并留给运维恢复，ERROR 日志携带 key）。修复前 finally 无条
    件删备份：清单行永久指向错位字节且无任何恢复来源。"""
    storage = _FlakyRestoreStorage(failures=99)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    _seed_committed_artifact(job_db, tmp_path, store, workspace_id="pr-ws", job_id="pr-job")
    _bump_generation("pr-job")
    (tmp_path / "new.json").write_bytes(b"new-bytes")

    with caplog.at_level(logging.ERROR, logger="server.app.executors._artifact_restore"):
        result = store.upload(
            workspace_id="pr-ws",
            job_id="pr-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "new.json",
            lease_id="lease-0",
        )

    assert result is None  # 闸拒决定本身不变
    assert storage.restore_attempts == 3  # 有界重试耗尽
    authority_key = "jobs/pr-ws/pr-job/out.json"
    assert storage.objects[authority_key] == b"new-bytes"  # 错位字节保留待恢复
    retained = [key for key in storage.objects if "/.rollback/" in key]
    assert len(retained) == 1  # 最后恢复源留存
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(authority_key in msg and retained[0] in msg for msg in errors)
    row = store.row_for_node("pr-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"old-bytes").hexdigest()  # 幸存行仍指旧字节
    # staging 源（上传通道）照常清理——它防的是「字节未 promote」，与备份不同族
    staging_leftovers = [
        key
        for key in storage.objects
        if key.startswith("jobs-staging/") and "/.rollback/" not in key
    ]
    assert not staging_leftovers


class _FailCopyAndRestoreStorage(FakeObjectStorage):
    """第二个 staging→authority copy 必失败；恢复 copy 持续失败。"""

    def __init__(self, fail_dest: str) -> None:
        super().__init__()
        self._fail_dest = fail_dest

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if "/.rollback/" in source_key:
            raise ConnectionError("s3 transient restore")
        if destination_key == self._fail_dest:
            raise ConnectionError("s3 transient")
        super().copy_object(source_key, destination_key)


def test_copy_failure_with_persistent_restore_failure_retains_backup(
    job_db: JobQueries,
) -> None:
    """锁内 copy 失败臂同族钉 + ack 歧义语义（#774 对抗复审 P1）：「copy 尝
    试过」的 key（k1 已落、k2 尝试即失败）一律进恢复集——恢复也持续失败
    时两者备份都保留（k2 可能已落字节，不能按冗余删）；从未尝试的 k3 备
    份照常删除（其 authority 未被触碰，删除前提成立）。修复前 finally 把
    全部备份一并删除，k2 的 ack 歧义形态连 ERROR 指针都不留。"""
    _seed_job(job_db, workspace_id="crf-ws", job_id="crf-job")
    _seed_lease(job_db, workspace_id="crf-ws", job_id="crf-job", lease_id="lease-1", generation=0)
    k1_auth = artifact_storage_key("crf-ws", "crf-job", "k1.json")
    k2_auth = artifact_storage_key("crf-ws", "crf-job", "k2.json")
    k3_auth = artifact_storage_key("crf-ws", "crf-job", "k3.json")
    storage = _FailCopyAndRestoreStorage(fail_dest=k2_auth)
    storage.objects.update(
        {
            k1_auth: b"old-1",
            k2_auth: b"old-2",
            k3_auth: b"old-3",
            "stg1": b"a1-new",
            "stg2": b"a2-new",
            "stg3": b"a3-new",
        }
    )

    def _row(name: str, key: str, payload: bytes) -> dict[str, Any]:
        return {
            "job_id": "crf-job",
            "node_key": "node_a",
            "name": name,
            "storage_key": key,
            "size_bytes": len(payload),
            "content_hash": hashlib.sha256(payload).hexdigest(),
        }

    rb = "jobs-staging/crf-ws/crf-job/e1/.rollback"
    with pytest.raises(ConnectionError, match=r"s3 transient$"):
        promote_to_authority_guarded(
            storage,
            TEST_DATABASE_URL,
            job_id="crf-job",
            lease_id="lease-1",
            copies=[
                AuthorityCopy(
                    name="k1.json",
                    staging_key="stg1",
                    authority_key=k1_auth,
                    rollback_key=f"{rb}/k1.json",
                ),
                AuthorityCopy(
                    name="k2.json",
                    staging_key="stg2",
                    authority_key=k2_auth,
                    rollback_key=f"{rb}/k2.json",
                ),
                AuthorityCopy(
                    name="k3.json",
                    staging_key="stg3",
                    authority_key=k3_auth,
                    rollback_key=f"{rb}/k3.json",
                ),
            ],
            rows=[
                _row("k1.json", k1_auth, b"a1-new"),
                _row("k2.json", k2_auth, b"a2-new"),
                _row("k3.json", k3_auth, b"a3-new"),
            ],
        )

    assert storage.objects[k1_auth] == b"a1-new"  # 恢复失败：错位字节保留待恢复
    assert storage.objects[k2_auth] == b"old-2"  # copy 尝试失败：fake 未落字节
    assert storage.objects[k3_auth] == b"old-3"  # 从未尝试：未被触碰
    assert {key for key in storage.objects if "/.rollback/" in key} == {
        f"{rb}/k1.json",
        f"{rb}/k2.json",
    }
    assert _manifest_row_count(job_db, "crf-job") == 0  # 事务回滚：零清单行


def _manifest_row_count(job_db: JobQueries, job_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "select count(*) as n from job_artifacts where job_id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return int(row["n"])


def test_db_error_during_registration_gets_single_shot_restore(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试分级（#774 对抗复审 P2）：psycopg 族异常 = 会话（连同按 key 锁）
    已不可信——恢复单发。退避重试只在锁仍持有时吸收瞬时存储故障；锁没了
    sleep 只放大迟到恢复踩并发新 promote 的窗口。钉住：登记阶段注入
    psycopg 失败时恢复恰好尝试一次，恢复失败后备份留存。"""
    import psycopg

    storage = _FlakyRestoreStorage(failures=99)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    _seed_committed_artifact(job_db, tmp_path, store, workspace_id="dbx-ws", job_id="dbx-job")
    (tmp_path / "new.json").write_bytes(b"new-bytes")

    def _dead_upsert(*args: Any, **kwargs: Any) -> None:
        raise psycopg.OperationalError("connection dead")

    monkeypatch.setattr(ap, "upsert_artifact_row_tx", _dead_upsert)

    with pytest.raises(psycopg.OperationalError, match="connection dead"):
        store.upload(
            workspace_id="dbx-ws",
            job_id="dbx-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "new.json",
            lease_id="lease-0",
        )

    assert storage.restore_attempts == 1  # 会话嫌疑面：单发，不退避
    assert len([key for key in storage.objects if "/.rollback/" in key]) == 1  # 备份留存


class _KeyboardInterruptCopyStorage(FakeObjectStorage):
    """staging→authority 的 promote copy 抛 KeyboardInterrupt（非 Exception 族）。"""

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if "/.rollback/" in source_key or "/.rollback/" in destination_key:
            super().copy_object(source_key, destination_key)  # 备份写/恢复读照常
            return
        raise KeyboardInterrupt


def test_keyboard_interrupt_mid_copy_still_restores(job_db: JobQueries) -> None:
    """BaseException 覆盖（#774 对抗复审 P2）：copy 中途 KeyboardInterrupt 也
    必须进补偿臂（恢复 + 原样上抛）——否则 finally 会在中间态未解除时删掉
    备份（codex #774 P1 同族）。钉住：旧字节回位、备份按删除前提清理（恢
    复成功）、中断原样传播。"""
    _seed_job(job_db, workspace_id="kbi-ws", job_id="kbi-job")
    _seed_lease(job_db, workspace_id="kbi-ws", job_id="kbi-job", lease_id="lease-1", generation=0)
    k1_auth = artifact_storage_key("kbi-ws", "kbi-job", "k1.json")
    storage = _KeyboardInterruptCopyStorage()
    storage.objects.update({k1_auth: b"old-1", "stg1": b"a1-new"})

    with pytest.raises(KeyboardInterrupt):
        promote_to_authority_guarded(
            storage,
            TEST_DATABASE_URL,
            job_id="kbi-job",
            lease_id="lease-1",
            copies=[
                AuthorityCopy(
                    name="k1.json",
                    staging_key="stg1",
                    authority_key=k1_auth,
                    rollback_key="jobs-staging/kbi-ws/kbi-job/e1/.rollback/k1.json",
                )
            ],
            rows=[
                {
                    "job_id": "kbi-job",
                    "node_key": "node_a",
                    "name": "k1.json",
                    "storage_key": k1_auth,
                    "size_bytes": 6,
                    "content_hash": hashlib.sha256(b"a1-new").hexdigest(),
                }
            ],
        )

    assert storage.objects[k1_auth] == b"old-1"  # 补偿臂恢复（单发）
    assert not [key for key in storage.objects if "/.rollback/" in key]  # 恢复成功即清理


def test_commit_time_failure_with_restore_failure_retains_backup(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit 时刻失败臂同族钉：锁外 best-effort 恢复持续失败时，回滚备份
    同样保留（连接死亡不改变「备份是最后恢复源」的事实）。"""
    _seed_job(job_db, workspace_id="ctf-ws", job_id="ctf-job")
    _seed_lease(job_db, workspace_id="ctf-ws", job_id="ctf-job", lease_id="lease-1", generation=0)
    storage = _FlakyRestoreStorage(failures=99)
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    (tmp_path / "old.json").write_bytes(b"old-bytes")
    assert (
        store.upload(
            workspace_id="ctf-ws",
            job_id="ctf-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "old.json",
        )
        is not None
    )
    (tmp_path / "new.json").write_bytes(b"new-bytes")

    real_write_transaction = ap.write_transaction

    @contextlib.contextmanager
    def commit_failing(dsn):
        inner = real_write_transaction(dsn)
        conn = inner.__enter__()
        try:
            yield conn
        finally:
            # 模拟 commit 时刻连接死亡：服务端事务回滚（不提交）。
            inner.__exit__(ConnectionError, ConnectionError("lost"), None)
        raise ConnectionError("simulated commit failure")

    monkeypatch.setattr(ap, "write_transaction", commit_failing)

    with pytest.raises(ConnectionError, match="simulated commit failure"):
        store.upload(
            workspace_id="ctf-ws",
            job_id="ctf-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "new.json",
            lease_id="lease-1",
        )

    assert storage.objects["jobs/ctf-ws/ctf-job/out.json"] == b"new-bytes"  # 恢复未成功
    assert len([key for key in storage.objects if "/.rollback/" in key]) == 1  # 备份留存
    row = store.row_for_node("ctf-job", "node_a", "out.json")
    assert row is not None  # 事务回滚：幸存清单行仍指向旧字节——备份留存的存在理由
    assert row["content_hash"] == hashlib.sha256(b"old-bytes").hexdigest()
