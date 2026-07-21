"""Project the claimed remote worker onto job detail nodes (read-only).

The badge next to a running node shows which remote worker claimed its
execution. The lease table has no worker column; the claimed worker lives in
``remote_executions.worker_id`` and joins back through the lease's
``execution_id``. Only active leases with a claimed execution yield a value —
queued executions and finished/expired leases project ``None``. For shard
nodes several leases share a ``node_key``; the oldest claimed shard's worker
is the representative value (a badge only needs one).
"""

from __future__ import annotations

from server.app.db.transaction import read_connection


def claimed_worker_map(db_path: str, job_id: str) -> dict[str, str]:
    """Map ``node_key`` to the worker that claimed its active remote execution."""
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select l.node_key as node_key, r.worker_id as worker_id"
            " from executor_leases l"
            " join remote_executions r on r.execution_id = l.execution_id"
            " where l.job_id = ? and l.status = 'active'"
            " and r.state = 'claimed' and r.worker_id is not null"
            " order by l.acquired_at, l.id",
            (job_id,),
        ).fetchall()
    worker_map: dict[str, str] = {}
    for row in rows:
        worker_map.setdefault(str(row["node_key"]), str(row["worker_id"]))
    return worker_map
