"""共享 promote primitive 的按 key 串行与恢复臂语义（#759 复审 P1-C/P2）。

P1-C：过期 promote 的失败恢复与并发新代次 promote 经
``artifact-authority:<key>`` advisory 锁全程互斥——恢复在构造上不可能
踩掉新代次已 copy 的 authority 字节。非规范产物名（``reports//out.json``）
在下载/登记前整批拒止。``promote_file_moves_guarded`` 的文件面用例在同
目录姊妹文件 ``test_file_promotion.py``；恢复失败时备份留存（codex #774
P1）的用例在 ``test_artifact_promotion_restore.py``。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import pytest

from server.app.agent_broker.remote_artifact_support import download_remote_artifact
from server.app.agent_broker.remote_artifacts import apply_worker_artifact_refs
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA

_separator = "&" if "?" in BASE_DATABASE_URL else "?"
# lock_timeout 必须大于主线程的观测窗口（10s）：被观测线程的锁等待若先被
# lock_timeout 杀死，慢观测会把绿测试打成假红（#759 对抗复审 P2）。
TIMED_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{_separator}options="
    f"{quote(f'-csearch_path={TEST_SCHEMA} -cdeadlock_timeout=50ms -clock_timeout=30s', safe='')}"
)


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


def _start(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = fn()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive(), "blocked transaction never resolved"


def _await_advisory_waiter(domain: str, timeout: float = 10.0) -> None:
    """等到有 backend 正等待该域名的 advisory 锁（pg_locks 观测）。"""
    with psycopg.connect(TIMED_DATABASE_URL, autocommit=True) as probe:
        row = probe.execute("select hashtext(%s)", (domain,)).fetchone()
        assert row is not None
        expected = int(row[0]) & 0xFFFFFFFFFFFFFFFF
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = probe.execute(
                "select classid, objid from pg_locks"
                " where locktype='advisory' and objsubid=1 and not granted"
            ).fetchall()
            for classid, objid in rows:
                if ((int(classid) << 32) | int(objid)) & 0xFFFFFFFFFFFFFFFF == expected:
                    return
            time.sleep(0.02)
    raise AssertionError(f"no waiter appeared for advisory lock {domain}")


class _RestoreBarrierStorage(FakeObjectStorage):
    """过期臂的恢复 copy 在按 key 锁内停下，等主线程观测到并发 promote 等锁。"""

    def __init__(self) -> None:
        super().__init__()
        self.restore_entered = threading.Event()
        self.release_restore = threading.Event()

    def copy_object(self, source_key: str, destination_key: str) -> None:
        is_restore = "/.rollback/" in source_key
        super().copy_object(source_key, destination_key)
        if is_restore:
            self.restore_entered.set()
            # 等不到放行即红（不静默退化为无序交错）。
            assert self.release_restore.wait(timeout=10), "main thread never released the restore"


def test_stale_restore_serialized_against_concurrent_promotion(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（#759 复审 P1-C）：过期 promote 闸拒后的恢复仍在按 key 锁内，
    并发的新代次 promote 必须等锁——恢复完成后新代次才备份/copy/登记，最终
    authority 与清单行都指向新代次字节（串行序「旧恢复 → 新提升」）。

    突变自检：没有按 key 锁时，新代次 promote 不会出现在 advisory 锁等待
    里（观测超时），且其 copy 可落在恢复之前——最终 authority 是被恢复的
    旧字节而清单行指向新字节（hash 断言失败）。"""
    _seed_job(job_db, workspace_id="ser-ws", job_id="ser-job")
    _seed_lease(
        job_db, workspace_id="ser-ws", job_id="ser-job", lease_id="lease-stale", generation=0
    )
    storage = _RestoreBarrierStorage()
    store = JobArtifactObjectStore(TIMED_DATABASE_URL, storage)
    (tmp_path / "out.json").write_bytes(b"old-bytes")
    assert (
        store.upload(
            workspace_id="ser-ws",
            job_id="ser-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "out.json",
        )
        is not None
    )
    # reset：bump 到 1，旧清单行保留（RMW/输入保护分支）；lease-stale 仍是 0 代。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("update jobs set execution_generation=1 where id='ser-job'")
    _seed_lease(
        job_db, workspace_id="ser-ws", job_id="ser-job", lease_id="lease-fresh", generation=1
    )

    def _stale_upload() -> Any:
        (tmp_path / "stale.json").write_bytes(b"stale-new-bytes")
        return store.upload(
            workspace_id="ser-ws",
            job_id="ser-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "stale.json",
            lease_id="lease-stale",
        )

    def _fresh_upload() -> Any:
        (tmp_path / "fresh.json").write_bytes(b"fresh-new-bytes")
        return store.upload(
            workspace_id="ser-ws",
            job_id="ser-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "fresh.json",
            lease_id="lease-fresh",
        )

    authority_key = "jobs/ser-ws/ser-job/out.json"
    thread_a, outcome_a = _start(_stale_upload)
    assert storage.restore_entered.wait(timeout=10)
    thread_b, outcome_b = _start(_fresh_upload)
    # B 必须卡在 artifact-authority 按 key 锁上（A 的恢复仍在锁内）。
    _await_advisory_waiter(f"artifact-authority:{authority_key}")
    storage.release_restore.set()
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_a["result"] is None  # 过期 promote 闸拒
    assert outcome_b.get("error") is None
    assert outcome_b["result"] is not None  # 新代次登记成功
    assert storage.objects[authority_key] == b"fresh-new-bytes"
    row = store.row_for_node("ser-job", "node_a", "out.json")
    assert row is not None
    assert row["size_bytes"] == len(b"fresh-new-bytes")
    assert row["content_hash"] == hashlib.sha256(b"fresh-new-bytes").hexdigest()
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]


def test_download_remote_artifact_rejects_noncanonical_name(tmp_path: Path) -> None:
    """#759 复审 P2：非规范名（``reports//out.json``）在下载前拒止。"""
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage(objects={"k": b"x"}))

    with pytest.raises(ValueError, match="unsafe expected output name"):
        download_remote_artifact(
            store, tmp_path, "reports//out.json", {"storage_key": "k", "content_hash": ""}
        )


def test_apply_worker_artifact_refs_rejects_noncanonical_name(tmp_path: Path) -> None:
    """#759 复审 P2：非规范名在 phase-1 整批拒止，不进入下载/提升/登记。"""
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage(objects={"k": b"x"}))

    names, failure = apply_worker_artifact_refs(
        store,
        runner="w1",
        workspace_id="ws",
        job_id="j1",
        node_key="n1",
        job_dir=tmp_path,
        expected=("reports/out.json",),
        output_artifacts={
            "reports//out.json": {"storage_key": "k", "size_bytes": 1, "content_hash": ""}
        },
        download=True,
        execution_id="exec-1",
        lease_id="lease-x",
    )

    assert failure is not None and "non-canonical" in failure.error_message
    assert names == {"reports//out.json"}


def test_commit_time_failure_restores_authority_and_cleans_backup(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#759 对抗复审 P1：登记事务 commit 时刻失败（连接死亡、服务端回滚）也
    必须恢复 authority copy——事务回滚后幸存的旧清单行仍指向旧字节；回滚
    备份照旧清理。单事务重构前 commit 在被 try 包住的登记函数内、有此覆
    盖；恢复臂必须盖住整个 with 块（含 __exit__ 的 commit）。"""
    import contextlib

    import server.app.executors._artifact_promotion as ap

    _seed_job(job_db, workspace_id="cmt-ws", job_id="cmt-job")
    _seed_lease(job_db, workspace_id="cmt-ws", job_id="cmt-job", lease_id="lease-1", generation=0)
    storage = FakeObjectStorage()
    store = JobArtifactObjectStore(TEST_DATABASE_URL, storage)
    (tmp_path / "out.json").write_bytes(b"old-bytes")
    assert (
        store.upload(
            workspace_id="cmt-ws",
            job_id="cmt-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "out.json",
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
            workspace_id="cmt-ws",
            job_id="cmt-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "new.json",
            lease_id="lease-1",
        )

    authority_key = "jobs/cmt-ws/cmt-job/out.json"
    assert storage.objects[authority_key] == b"old-bytes"  # 恢复臂覆盖 commit 失败
    row = store.row_for_node("cmt-job", "node_a", "out.json")
    assert row is not None
    assert row["size_bytes"] == len(b"old-bytes")  # 事务回滚，旧行幸存且指向旧字节
    assert row["content_hash"] == hashlib.sha256(b"old-bytes").hexdigest()
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]


class _FailSecondCopyBarrierStorage(FakeObjectStorage):
    """A 的第二个 promote copy 必失败；恢复 copy 在按 key 锁内停下等观测。"""

    def __init__(self, fail_dest: str) -> None:
        super().__init__()
        self._fail_dest = fail_dest
        self.restore_entered = threading.Event()
        self.release_restore = threading.Event()

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if destination_key == self._fail_dest and "/.rollback/" not in source_key:
            raise ConnectionError("s3 transient")
        is_restore = "/.rollback/" in source_key
        super().copy_object(source_key, destination_key)
        if is_restore:
            self.restore_entered.set()
            # 等不到放行即红（不静默退化为无序交错）。
            assert self.release_restore.wait(timeout=10), "main thread never released the restore"


def test_copy_failure_restore_serialized_against_concurrent_promotion(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（#759 对抗复审 P1）：copy 中途失败的恢复必须在事务死亡前
    （按 key 锁仍持有）完成——并发的同 key promote 必须等锁。恢复若拖到
    事务回滚后（锁已释放）执行，新代次 promote 的 copy 可落在恢复之前，
    最终 authority 是被恢复的旧字节而清单行指向新字节。

    突变自检：恢复在锁外执行时，B 不会出现在 advisory 锁等待里（观测超
    时），且最终 hash 断言失败。"""
    from server.app.executors._artifact_promotion import (
        AuthorityCopy,
        promote_to_authority_guarded,
    )
    from server.app.services.job_artifact_objects import artifact_storage_key

    _seed_job(job_db, workspace_id="cfx-ws", job_id="cfx-job")
    _seed_lease(job_db, workspace_id="cfx-ws", job_id="cfx-job", lease_id="lease-1", generation=0)
    k1_auth = artifact_storage_key("cfx-ws", "cfx-job", "k1.json")
    k2_auth = artifact_storage_key("cfx-ws", "cfx-job", "k2.json")
    storage = _FailSecondCopyBarrierStorage(fail_dest=k2_auth)
    storage.objects.update(
        {
            k1_auth: b"old-1",
            k2_auth: b"old-2",
            "stg1": b"a1-new",
            "stg2": b"a2-new",
        }
    )
    store = JobArtifactObjectStore(TIMED_DATABASE_URL, storage)

    def _row(name: str, key: str, payload: bytes) -> dict[str, Any]:
        return {
            "job_id": "cfx-job",
            "node_key": "node_a",
            "name": name,
            "storage_key": key,
            "size_bytes": len(payload),
            "content_hash": hashlib.sha256(payload).hexdigest(),
        }

    def _failing_promote() -> Any:
        return promote_to_authority_guarded(
            storage,
            TEST_DATABASE_URL,
            job_id="cfx-job",
            lease_id="lease-1",
            copies=[
                AuthorityCopy(
                    name="k1.json",
                    staging_key="stg1",
                    authority_key=k1_auth,
                    rollback_key="jobs-staging/cfx-ws/cfx-job/e1/.rollback/k1.json",
                ),
                AuthorityCopy(
                    name="k2.json",
                    staging_key="stg2",
                    authority_key=k2_auth,
                    rollback_key="jobs-staging/cfx-ws/cfx-job/e1/.rollback/k2.json",
                ),
            ],
            rows=[_row("k1.json", k1_auth, b"a1-new"), _row("k2.json", k2_auth, b"a2-new")],
        )

    def _fresh_upload() -> Any:
        (tmp_path / "fresh.json").write_bytes(b"fresh-bytes")
        return store.upload(
            workspace_id="cfx-ws",
            job_id="cfx-job",
            node_key="node_a",
            name="k1.json",
            local_path=tmp_path / "fresh.json",
            lease_id="lease-1",
        )

    thread_a, outcome_a = _start(_failing_promote)
    assert storage.restore_entered.wait(timeout=10)
    thread_b, outcome_b = _start(_fresh_upload)
    # A 的恢复仍在按 key 锁内：B 必须卡在 artifact-authority 锁上。
    _await_advisory_waiter(f"artifact-authority:{k1_auth}")
    storage.release_restore.set()
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is not None  # 第二个 copy 的注入失败原样上抛
    assert outcome_b.get("error") is None
    assert outcome_b["result"] is not None
    assert storage.objects[k1_auth] == b"fresh-bytes"  # 恢复（锁内）→ B 覆盖
    row = store.row_for_node("cfx-job", "node_a", "k1.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"fresh-bytes").hexdigest()
    assert storage.objects[k2_auth] == b"old-2"  # 未 promoted 的 key 不被恢复
    assert store.row_for_node("cfx-job", "node_a", "k2.json") is None
    assert not [key for key in storage.objects if "/.rollback/" in key]


# ---------------------------------------------------------------------------
# codex #774 P1×2：并发重试的 staging/rollback key 按调用唯一化
# ---------------------------------------------------------------------------


def test_same_lease_uploads_get_per_invocation_staging_and_rollback_keys(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造性钉（本地臂）：同 lease 的每次 upload 派生独立 staging/rollback
    key——并发重试的 put_stream（锁外）互不覆盖、finally 清理互不误删。
    共享 key 时后写者字节会被先写者 promote 进 authority、却登记先写者的
    size/hash（清单行与字节错位）。"""
    _seed_job(job_db, workspace_id="pik-ws", job_id="pik-job")
    _seed_lease(job_db, workspace_id="pik-ws", job_id="pik-job", lease_id="lease-1", generation=0)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "server.app.services.job_artifact_objects.upload_via_staging_guarded",
        lambda *args, **kwargs: captured.append(kwargs) or None,
    )
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    (tmp_path / "a.json").write_bytes(b"a")
    (tmp_path / "b.json").write_bytes(b"b")

    store.upload(
        workspace_id="pik-ws",
        job_id="pik-job",
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "a.json",
        lease_id="lease-1",
    )
    store.upload(
        workspace_id="pik-ws",
        job_id="pik-job",
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "b.json",
        lease_id="lease-1",
    )

    assert len(captured) == 2
    assert captured[0]["staging_key"] != captured[1]["staging_key"]
    assert captured[0]["rollback_key"] != captured[1]["rollback_key"]
    assert all("lease-1" in str(call["staging_key"]) for call in captured)
    assert all("lease-1" in str(call["rollback_key"]) for call in captured)


def test_remote_promote_gets_per_invocation_rollback_keys(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造性钉（远端臂）：并发 /result 重试（同 execution、同名产物）的
    回滚备份 key 按调用唯一化——先提交者的锁外清理删不到后者的备份，
    后者闸拒/登记失败时恢复有备份可取。staging key 保持协议固定的
    per-execution 落点（Worker 上传通道，重试字节相同）。"""
    from server.app.agent_broker.remote_artifact_promote import promote_all

    _seed_job(job_db, workspace_id="rbk-ws", job_id="rbk-job")
    _seed_lease(job_db, workspace_id="rbk-ws", job_id="rbk-job", lease_id="lease-1", generation=0)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "server.app.agent_broker.remote_artifact_promote.promote_to_authority_guarded",
        lambda *args, **kwargs: captured.append(kwargs) or [],
    )
    store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    remote = {
        "out.json": {
            "storage_key": "jobs-staging/rbk-ws/rbk-job/exec-1/out.json",
            "size_bytes": 1,
            "content_hash": "",
        }
    }

    hashes = {"out.json": ""}
    assert promote_all(
        store, "rbk-ws", "rbk-job", "node_a", tmp_path, remote, {}, hashes, "e1", "lease-1"
    )
    assert promote_all(
        store, "rbk-ws", "rbk-job", "node_a", tmp_path, remote, {}, hashes, "e1", "lease-1"
    )

    assert len(captured) == 2
    rollback_keys = [str(call["copies"][0].rollback_key) for call in captured]
    assert rollback_keys[0] != rollback_keys[1]
    staging_keys = [str(call["copies"][0].staging_key) for call in captured]
    assert staging_keys == [remote["out.json"]["storage_key"]] * 2  # 协议落点不变


class _CopyAfterPutBarrierStorage(FakeObjectStorage):
    """A（小负载）的 staging→authority copy 停住，等 B（大负载）的 put 落盘。

    确定性排出 codex #774 P1 的字节面交错：A 的 staging 字节已写、promote
    copy 未发时，B 的 put 覆盖同一 staging key（修复前共享）——A 把 B 的
    字节 copy 进 authority 却登记自己的 size/hash。barrier 必须挡在 copy
    侧：挡 put 侧会让 A 的字节成为 staging 最终内容，交错退化为无害序
    （测试在修复前也绿，#774 对抗复审 F1）。等不到放行即 AssertionError
    （红方向，不静默假绿）。"""

    def __init__(self) -> None:
        super().__init__()
        self.a_copy_entered = threading.Event()
        self.b_put_done = threading.Event()
        self.allow_a_copy = threading.Event()
        self._puts = 0
        self._copy_consumed = False

    def put_stream(
        self, storage_key: str, stream: Any, size_bytes: int, content_type: str = ""
    ) -> None:
        super().put_stream(storage_key, stream, size_bytes, content_type)
        self._puts += 1
        if self._puts == 2:  # A 的 put 先于其 copy；第二个 put 必是 B 的
            self.b_put_done.set()

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if (
            not self._copy_consumed
            and "jobs-staging/" in source_key
            and "/.rollback/" not in destination_key
        ):
            self._copy_consumed = True
            self.a_copy_entered.set()
            assert self.allow_a_copy.wait(timeout=10), "main thread never released A's copy"
        super().copy_object(source_key, destination_key)


def test_concurrent_same_lease_uploads_never_mix_bytes_and_rows(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（codex #774 P1）：同 lease 两个并发 upload（同名、不同字
    节）——A 的 staging put 完成、promote copy 待发时，B 的 put 落盘。
    per-invocation staging key 下两次 promote 各自读自己的字节——最终
    authority 字节与清单行的 size/hash 必定同源（whichever promote 后提
    交）。共享 staging key 时 B 的 put 覆盖了 A 的 staging：A 把 B 的字
    节 promote 进 authority 却登记自己的 size/hash（清单行与 authority
    字节永久错位），且 A 的 finally 还会删掉 B 等待提升的 staging 源。"""
    _seed_job(job_db, workspace_id="mix-ws", job_id="mix-job")
    _seed_lease(job_db, workspace_id="mix-ws", job_id="mix-job", lease_id="lease-1", generation=0)
    payload_small = b"A" * 10
    payload_large = b"B" * 20
    storage = _CopyAfterPutBarrierStorage()
    store = JobArtifactObjectStore(TIMED_DATABASE_URL, storage)
    (tmp_path / "small.json").write_bytes(payload_small)
    (tmp_path / "large.json").write_bytes(payload_large)

    def _upload(local: str) -> Any:
        return store.upload(
            workspace_id="mix-ws",
            job_id="mix-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / local,
            lease_id="lease-1",
        )

    thread_a, outcome_a = _start(lambda: _upload("small.json"))
    assert storage.a_copy_entered.wait(timeout=10)
    thread_b, outcome_b = _start(lambda: _upload("large.json"))
    assert storage.b_put_done.wait(timeout=10)
    storage.allow_a_copy.set()  # A 此刻 copy 的 staging 内容在修复前已是 B 的字节
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_b.get("error") is None
    authority_key = "jobs/mix-ws/mix-job/out.json"
    authority = storage.objects[authority_key]
    assert authority in {payload_small, payload_large}
    row = store.row_for_node("mix-job", "node_a", "out.json")
    assert row is not None
    # 不变量：清单行与 authority 字节同源——共享 staging key 时这里错位。
    assert row["size_bytes"] == len(authority)
    assert row["content_hash"] == hashlib.sha256(authority).hexdigest()
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]


class _RollbackClobberStorage(FakeObjectStorage):
    """T1 的回滚清理 delete 停住；主线程观测到 T2 的备份写完成后放行。

    copy_object 的 destination 含 ``/.rollback/`` = 备份写；source 含
    ``/.rollback/`` = 恢复读（比照 _RestoreBarrierStorage 的约定）。"""

    def __init__(self) -> None:
        super().__init__()
        self.watch_t2_backup = False
        self.t1_delete_entered = threading.Event()
        self.allow_t1_delete = threading.Event()
        self.t1_deleted = threading.Event()
        self.t2_backup_written = threading.Event()
        self.allow_t2_copy = threading.Event()
        self._t1_delete_consumed = False

    def delete_object(self, storage_key: str) -> None:
        if "/.rollback/" in storage_key and not self._t1_delete_consumed:
            self._t1_delete_consumed = True
            self.t1_delete_entered.set()
            # 等不到放行即红（不静默退化为无序交错）。
            assert self.allow_t1_delete.wait(timeout=10), "main thread never released T1's delete"
            super().delete_object(storage_key)
            self.t1_deleted.set()
            return
        super().delete_object(storage_key)

    def copy_object(self, source_key: str, destination_key: str) -> None:
        if "/.rollback/" in destination_key and self.watch_t2_backup:
            super().copy_object(source_key, destination_key)
            self.t2_backup_written.set()
            return
        if (
            self.watch_t2_backup
            and "jobs-staging/" in source_key
            and "/.rollback/" not in source_key
        ):
            # T2 的 staging→authority copy 等主线程把代次 bump 提交——闸拒
            # 时序确定性（否则 bump 与 T2 的闸内复查竞争，T2 可能抢跑登记）。
            assert self.allow_t2_copy.wait(timeout=10), "main thread never released T2's copy"
        if "/.rollback/" in source_key:
            # 恢复读必须等到 T1 的锁外清理完成——确定性排出「先清理后恢复」。
            assert self.t1_deleted.wait(timeout=10), "T1's cleanup never completed"
        super().copy_object(source_key, destination_key)


def test_concurrent_retry_rollback_backup_survives_first_committer_cleanup(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """交错用例（codex #774 P1）：T1 promote 提交并释放按 key 锁后，T2
    （同 lease 重试）取得锁写入自己的回滚备份；T1 的锁外清理此刻才执行。
    per-invocation rollback key 下 T1 只删自己的备份，T2 闸拒后按备份恢
    复——authority 与幸存的 T1 清单行逐字节一致。共享 rollback key 时
    T1 的清理连 T2 的备份一起删掉，T2 恢复无备份可取：authority 停在
    T2 字节而清单行指向 T1（hash 断言失败）。"""
    _seed_job(job_db, workspace_id="clb-ws", job_id="clb-job")
    _seed_lease(job_db, workspace_id="clb-ws", job_id="clb-job", lease_id="lease-1", generation=0)
    storage = _RollbackClobberStorage()
    store = JobArtifactObjectStore(TIMED_DATABASE_URL, storage)
    (tmp_path / "seed.json").write_bytes(b"old-bytes")
    assert (
        store.upload(
            workspace_id="clb-ws",
            job_id="clb-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / "seed.json",
        )
        is not None
    )
    (tmp_path / "one.json").write_bytes(b"one")
    (tmp_path / "two.json").write_bytes(b"two")

    def _upload(local: str) -> Any:
        return store.upload(
            workspace_id="clb-ws",
            job_id="clb-job",
            node_key="node_a",
            name="out.json",
            local_path=tmp_path / local,
            lease_id="lease-1",
        )

    thread_a, outcome_a = _start(lambda: _upload("one.json"))
    assert storage.t1_delete_entered.wait(timeout=10)  # T1 已提交，卡在锁外清理
    storage.watch_t2_backup = True
    thread_b, outcome_b = _start(lambda: _upload("two.json"))
    assert storage.t2_backup_written.wait(timeout=10)  # T2 已写入自己的回滚备份
    # T2 的闸内登记前 bump 代次 → T2 闸拒、必须按备份恢复。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("update jobs set execution_generation=1 where id='clb-job'")
    storage.allow_t2_copy.set()  # T2 继续 copy → 闸拒 → 恢复
    storage.allow_t1_delete.set()  # T1 的清理落在 T2 的备份与恢复之间
    _join(thread_a)
    _join(thread_b)

    assert outcome_a.get("error") is None
    assert outcome_a["result"] is not None  # T1 提交
    assert outcome_b.get("error") is None
    assert outcome_b["result"] is None  # T2 闸拒
    authority_key = "jobs/clb-ws/clb-job/out.json"
    assert storage.objects[authority_key] == b"one"  # T2 按备份恢复，未停在 T2 字节
    row = store.row_for_node("clb-job", "node_a", "out.json")
    assert row is not None
    assert row["content_hash"] == hashlib.sha256(b"one").hexdigest()
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]
