"""提交后 sweep 的删除保护集（codex #776 复审 P2-A；queries 预算拆分自
``job_artifact_keys``）。

升级事务提交后作业立即可被调度：重置节点的新 attempt 可能在 sweep
执行前已写出同名新字节（完成臂登记了清单行 / 在跑节点已写文件但行
未登记），无条件 unlink 会误删新代次产物。复核在 job-mutation 锁内
进行（claim 侧同锁——复核与删除之间 claim 不可插入）：有清单行
（新 attempt 已登记）或任一新图生产者节点 running/completed（文件是
新字节）的名进保护集。文件删除不是事务的一部分，但锁保证判定到
删除之间没有新 claim 起跑。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from server.app.db.connection import DatabaseConnection
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
