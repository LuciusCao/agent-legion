"""EXEC-GENERATION-001 产物写面与空 fan-out 完成面的代次闸（#645 P2-b/P3，#759 复审 P1-B）。

P2-b（本地 code 孤儿执行的迟来上传）：心跳丢失后沙箱子进程是协作式取消，
可跑完再进 ``_check_outputs`` → ``upload_produced_artifacts``。写闸
（lease active + 心跳新鲜 + 落戳代次 == jobs 现值）不过则整批不上传。
P1-B 起 lease 臂上传改走 staging：字节先落 per-lease staging key，再经共享
primitive（``executors._artifact_promotion.promote_to_authority_guarded``）
备份 → 锁外 copy → 锁内复查 + 登记 → 闸拒按回滚备份恢复 authority——
「入口闸通过后、登记前 reset 提交」的窗口既不复活已删清单行，也不让保留
的旧行指向被污染的字节。
lease_lost 的正常收尾语义（runtime 置失败结果、finish CAS）不在本文件，
由 tests/executors/test_executor_runtime.py 钉住。

P3（空 fan-out 完成无 CAS）：``complete_empty_shard_node`` 的锁 + 代次 CAS
保证 reset 交错时新代次的 pending 节点不被无执行翻成 completed；
``materialize_shards_guarded`` 把锁提到 node_shards 行写之前（锁序：
job-mutation advisory → 行锁；mutation 侧持同锁删 shard 行）。

交错手法比照 tests/db/test_execution_generation_races.py：TIMED_DATABASE_URL
带 deadlock_timeout/lock_timeout，同步点走 pg_locks 观测或 storage hook
内同步提交 reset，不用裸 sleep。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg

from server.app.db.transaction import write_transaction
from server.app.executors._lease_control import lock_job_mutation_and_read_generation
from server.app.executors._lease_shards import complete_empty_shard_node
from server.app.executors.artifact_mirror import upload_produced_artifacts
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import lease_guarded_mutation, mark_nodes_for_rerun
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.workflow_worker.shard_fanout import materialize_shards_guarded
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA

_separator = "&" if "?" in BASE_DATABASE_URL else "?"
TIMED_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{_separator}options="
    f"{quote(f'-csearch_path={TEST_SCHEMA} -cdeadlock_timeout=50ms -clock_timeout=5s', safe='')}"
)


def _seed_job(
    job_db: JobQueries, *, workspace_id: str, job_id: str, node_key: str = "node_a"
) -> None:
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
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _seed_lease(
    job_db: JobQueries,
    *,
    workspace_id: str,
    job_id: str,
    node_key: str = "node_a",
    lease_id: str = "lease-1",
) -> None:
    with job_db.connect() as conn:
        cursor = conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json, log_path,"
            " run_dir, session_dir, started_at)"
            " values (%s, %s, 'running', '[]', '', '', '', current_timestamp) returning id",
            (job_id, node_key),
        )
        conn.execute(
            "insert into executor_leases(id, execution_id, executor_id, workspace_id,"
            " job_id, node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at)"
            " values (%s, %s, 'code', %s, %s, %s, %s, 'active', current_timestamp,"
            " current_timestamp, current_timestamp + interval '1 hour')",
            (lease_id, f"exec-{lease_id}", workspace_id, job_id, node_key, cursor.fetchone()["id"]),
        )


def _store(objects: dict[str, bytes] | None = None) -> JobArtifactObjectStore:
    return JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage(objects=objects))


def _job_row(job_id: str) -> dict[str, Any]:
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select status, execution_generation from jobs where id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _node_row(job_id: str, node_key: str) -> dict[str, Any]:
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select status, execution_generation from job_nodes where job_id=%s and node_key=%s",
            (job_id, node_key),
        ).fetchone()
    assert row is not None
    return dict(row)


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
    assert not thread.is_alive(), "B-side transaction never resolved"


def _await_job_mutation_waiter(job_id: str, timeout: float = 10.0) -> None:
    """等到有 backend 正等待该 job 的 job-mutation advisory 锁（pg_locks 观测）。"""
    with psycopg.connect(TIMED_DATABASE_URL, autocommit=True) as probe:
        row = probe.execute("select hashtext(%s)", (f"job-mutation:{job_id}",)).fetchone()
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
    raise AssertionError(f"no backend waited on job-mutation:{job_id} within {timeout}s")


# ---------------------------------------------------------------------------
# P2-b：本地 code 孤儿执行的迟来上传
# ---------------------------------------------------------------------------


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


class _ResetAfterStagingPutStorage(FakeObjectStorage):
    """staging put 落字节的同一瞬间在另一连接提交 reset。

    精确命中 P1-B 窗口：入口闸已通过、staging 字节已写、promote（备份 →
    copy → 锁内登记）尚未开始。``delete_row`` 选分支：True 模拟 clean 重置
    （删清单行），False 模拟 RMW/输入保护保留旧行（只 bump 代次）。
    ``armed`` 让种下旧行/旧字节的种子上传不触发 reset。
    """

    def __init__(self, *, job_id: str, delete_row: bool) -> None:
        super().__init__()
        self._job_id = job_id
        self._delete_row = delete_row
        self.armed = False

    def put_stream(
        self, storage_key: str, stream: Any, size_bytes: int, content_type: str = ""
    ) -> None:
        super().put_stream(storage_key, stream, size_bytes, content_type)
        if not self.armed:
            return
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "update jobs set execution_generation=execution_generation+1 where id=%s",
                (self._job_id,),
            )
            if self._delete_row:
                conn.execute("delete from job_artifacts where job_id=%s", (self._job_id,))


def _seed_authority_object(
    store: JobArtifactObjectStore,
    tmp_path: Path,
    *,
    workspace_id: str,
    job_id: str,
    payload: bytes,
) -> dict[str, Any]:
    """无 lease 直写种子：旧清单行 + 旧 authority 字节；返回旧清单行。"""
    (tmp_path / "out.json").write_bytes(payload)
    row = store.upload(
        workspace_id=workspace_id,
        job_id=job_id,
        node_key="node_a",
        name="out.json",
        local_path=tmp_path / "out.json",
    )
    assert row is not None
    return row


def _assert_no_staging_residue(storage: FakeObjectStorage) -> None:
    assert not [key for key in storage.objects if key.startswith("jobs-staging/")]


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


# ---------------------------------------------------------------------------
# P3：空 fan-out 完成的代次 CAS
# ---------------------------------------------------------------------------


def test_empty_shard_completion_rejected_after_reset(job_db: JobQueries) -> None:
    """reset bump 代次后，旧代次的空 fan-out 完成被 CAS 拒绝：节点保持
    pending（新戳），不被无执行翻成 completed；按新代次调用则照常完成。"""
    _seed_job(job_db, workspace_id="gate5-ws", job_id="gate5-job")
    with lease_guarded_mutation(
        TEST_DATABASE_URL, "gate5-job", datetime.now(UTC), reject_running_nodes=True
    ) as conn:
        mark_nodes_for_rerun(conn, "gate5-job", ["node_a"], {"node_a": []})
    assert _job_row("gate5-job")["execution_generation"] == 1

    with write_transaction(TEST_DATABASE_URL) as conn:
        applied = complete_empty_shard_node(conn, "gate5-job", "node_a", 0)

    assert applied is False
    node = _node_row("gate5-job", "node_a")
    assert node["status"] == "pending"
    assert int(node["execution_generation"]) == 1

    with write_transaction(TEST_DATABASE_URL) as conn:
        applied = complete_empty_shard_node(conn, "gate5-job", "node_a", 1)
    assert applied is True
    assert _node_row("gate5-job", "node_a")["status"] == "completed"


def test_guarded_fanout_waits_on_mutation_lock_then_skips(job_db: JobQueries) -> None:
    """交错用例（锁序 + CAS）：mutation 持 job-mutation 锁未提交时，guard 的
    物化事务必须先等锁（证明 advisory 锁先于 node_shards 行写），mutation
    提交（代次 → 1）后读到代次不符整段跳过——不物化、不完成，节点保持
    pending 新戳。最终状态 == 串行序「reset → 旧代次 fan-out 被跳过」。"""
    _seed_job(job_db, workspace_id="gate6-ws", job_id="gate6-job")
    with lease_guarded_mutation(
        TIMED_DATABASE_URL, "gate6-job", datetime.now(UTC), reject_running_nodes=True
    ) as conn_a:
        mark_nodes_for_rerun(conn_a, "gate6-job", ["node_a"], {"node_a": []})

        def _late_fanout() -> None:
            with write_transaction(TIMED_DATABASE_URL) as conn_b:
                # 非空输入：只有 wrapper 的前置闸能拦住物化（空输入会落到
                # complete_empty_shard_node 的内层 CAS，测不到闸的锁序）。
                materialize_shards_guarded(conn_b, "gate6-job", "node_a", [{"i": 0}], 4, 0)

        thread, outcome = _start(_late_fanout)
        _await_job_mutation_waiter("gate6-job")  # B 卡在 advisory 锁上（未写行）
    _join(thread)

    assert outcome.get("error") is None
    with write_transaction(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select count(*) as cnt from node_shards where job_id='gate6-job'"
        ).fetchone()
    assert int(row["cnt"]) == 0  # 未物化
    node = _node_row("gate6-job", "node_a")
    assert node["status"] == "pending"  # 未被无执行翻成 completed
    assert int(node["execution_generation"]) == 1


def test_guarded_fanout_materializes_and_completes_on_current_epoch(
    job_db: JobQueries,
) -> None:
    """对照组：代次一致时物化与空 fan-out 完成照常（空输入 → 节点完成；
    非空输入 → shard 行落库、节点保持 pending 待认领）。"""
    _seed_job(job_db, workspace_id="gate7-ws", job_id="gate7-job")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values ('gate7-job', 'node_b')")
        materialize_shards_guarded(conn, "gate7-job", "node_a", [], 4, 0)
        materialize_shards_guarded(conn, "gate7-job", "node_b", [{"i": 0}, {"i": 1}], 4, 0)

    assert _node_row("gate7-job", "node_a")["status"] == "completed"
    assert _node_row("gate7-job", "node_b")["status"] == "pending"
    with write_transaction(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select shard_index, status from node_shards"
            " where job_id='gate7-job' and node_key='node_b' order by shard_index"
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {"shard_index": 0, "status": "pending"},
        {"shard_index": 1, "status": "pending"},
    ]
