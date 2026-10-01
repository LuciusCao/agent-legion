"""提交后清理的锁内复核 guard（queries 预算叶子模块）。

两个 guard 同族：突变（rerun/upgrade/run-to/rework）的事务提交后，作业
立即可被调度，提交后清理（本地文件 sweep / 权威对象删除）与新一轮执行
的写面之间存在竞态——清理侧必须在动作前于锁内重新校验：

- ``sweep_delete_guard``（codex #776 复审 P2-A）：本地文件 sweep 在
  job-mutation 锁内复核（claim 侧同锁，判定到删除之间 claim 不可插入）；
- ``authority_delete_guard``（codex #776 R7 P2-A）：权威对象删除与
  promote 共享 ``artifact-authority:<key>`` 锁（② 的 promotion 协议把
  copy 与清单登记串行在同一把按 key 锁内）——锁不可得（在途 promote
  已 copy 未登记）或锁内复核到存活清单行，都禁止删除。只取
  artifact-authority 单锁、不取 job-mutation，与 promote 的
  artifact-authority → job-mutation 偏序不成环。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from server.app.db.connection import DatabaseConnection
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction
from server.app.jobs.queries.connection import ConnectionQueriesMixin


@contextmanager
def sweep_delete_guard(
    path: str,
    job_id: str,
    sweep_names: frozenset[str] | set[str],
    producers: dict[str, list[str]],
) -> Iterator[frozenset[str]]:
    """job-mutation 锁内算出保护集并持锁到调用方删完（见模块 docstring）。"""
    with write_transaction(path) as conn:
        conn.execute("select pg_advisory_xact_lock(hashtext('job-mutation:' || %s))", (job_id,))
        yield _sweep_protected_names(conn, job_id, sweep_names, producers)


def _sweep_protected_names(
    conn: DatabaseConnection,
    job_id: str,
    sweep_names: frozenset[str] | set[str],
    producers: dict[str, list[str]],
) -> frozenset[str]:
    names = sorted(set(sweep_names))
    protected = {
        str(row["name"])
        for row in conn.execute(
            "select name from job_artifacts where job_id=%s and name = any(%s)",
            (job_id, names),
        ).fetchall()
    }
    producer_keys = sorted({key for keys in producers.values() for key in keys})
    statuses: dict[str, str] = {}
    if producer_keys:
        statuses = {
            str(row["node_key"]): str(row["status"])
            for row in conn.execute(
                "select node_key, status from job_nodes where job_id=%s and node_key = any(%s)",
                (job_id, producer_keys),
            ).fetchall()
        }
    for name in names:
        if name not in protected and any(
            statuses.get(key) in ("running", "completed") for key in producers.get(name, [])
        ):
            protected.add(name)
    return frozenset(protected)


class SweepGuardQueriesMixin(ConnectionQueriesMixin):
    def sweep_delete_guard(
        self,
        job_id: str,
        sweep_names: frozenset[str] | set[str],
        producers: dict[str, list[str]],
    ) -> AbstractContextManager[frozenset[str]]:
        return sweep_delete_guard(self._path, job_id, sweep_names, producers)


@contextmanager
def authority_delete_guard(path: ConnectSource, storage_key: str) -> Iterator[bool]:
    """权威对象删除前的按 key 互斥（codex #776 R7 P2-A）。

    与 promote 共享 ``artifact-authority:<key>`` 锁：yield False = 在途
    promote 正持锁（其 copy 已落地、登记未提交，清单探针此刻必 miss，
    删除会误删新字节）——调用方跳过该 key（保守方向：旧对象成孤儿由
    bucket lifecycle 兜底）。yield True = 锁已持有，调用方在 with 块内
    复核清单（自己的探针 seam）并完成删除；锁保证复核到删除之间没有
    promote 插入。try-lock 不等待：提交后清理是 best-effort，不为在途
    promote 的大字节 copy 阻塞请求线程。
    """
    with write_transaction(path) as conn:
        got = conn.execute(
            "select pg_try_advisory_xact_lock(hashtext(%s))",
            (f"artifact-authority:{storage_key}",),
        ).fetchone()
        yield bool(got and got["pg_try_advisory_xact_lock"])
