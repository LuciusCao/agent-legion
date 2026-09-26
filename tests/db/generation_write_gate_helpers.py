"""EXEC-GENERATION 写闸测试的共享种子/同步工具（自
test_generation_write_gates.py 拆出，#779 codex 列车复审 P1-4——文件
超 800 拆分线，按写面拆成 upload/fanout/finish 三姊妹文件）。

供 tests/db 下 generation write-gate 一族测试文件共用的 job/lease 种子、
对象存储桩、行读取与线程/pg_locks 同步点；命名保持下划线前缀（沿用被拆
文件的用例现场，零改动迁移）。比照 tests/db/completion_helpers.py 的
既有共享模式。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg

from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
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
