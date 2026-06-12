from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any


def get_workspace_executor_configuration(
    conn: sqlite3.Connection, workspace_id: str
) -> dict[str, list[dict[str, Any]]]:
    allocations = conn.execute(
        "select workspace_id, executor_id, concurrency_limit "
        "from workspace_executor_allocations where workspace_id=? order by executor_id",
        (workspace_id,),
    ).fetchall()
    bindings = conn.execute(
        "select pipeline_key, node_key, executor_id "
        "from workspace_node_bindings where workspace_id=? order by pipeline_key, node_key",
        (workspace_id,),
    ).fetchall()
    node_limits = conn.execute(
        "select pipeline_key, node_key, concurrency_limit "
        "from workspace_node_limits where workspace_id=? order by pipeline_key, node_key",
        (workspace_id,),
    ).fetchall()
    return {
        "allocations": [dict(row) for row in allocations],
        "bindings": [dict(row) for row in bindings],
        "node_limits": [dict(row) for row in node_limits],
    }


def replace_workspace_executor_configuration(
    conn: sqlite3.Connection,
    workspace_id: str,
    allocations: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    node_limits: Sequence[Mapping[str, Any]],
) -> None:
    conn.execute("delete from workspace_node_limits where workspace_id=?", (workspace_id,))
    conn.execute("delete from workspace_node_bindings where workspace_id=?", (workspace_id,))
    conn.execute("delete from workspace_executor_allocations where workspace_id=?", (workspace_id,))
    conn.executemany(
        "insert into workspace_executor_allocations "
        "(workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
        [(workspace_id, row["executor_id"], row["concurrency_limit"]) for row in allocations],
    )
    conn.executemany(
        "insert into workspace_node_bindings "
        "(workspace_id, pipeline_key, node_key, executor_id) values (?, ?, ?, ?)",
        [
            (workspace_id, row["pipeline_key"], row["node_key"], row["executor_id"])
            for row in bindings
        ],
    )
    conn.executemany(
        "insert into workspace_node_limits "
        "(workspace_id, pipeline_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
        [
            (workspace_id, row["pipeline_key"], row["node_key"], row["concurrency_limit"])
            for row in node_limits
        ],
    )
