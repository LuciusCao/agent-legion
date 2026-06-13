import sqlite3
from dataclasses import dataclass, field


@dataclass
class ExistingConfiguration:
    allocations: set[str] = field(default_factory=set)
    bindings: set[tuple[str, str]] = field(default_factory=set)
    node_limits: set[tuple[str, str]] = field(default_factory=set)


def collect_existing_configuration(
    conn: sqlite3.Connection, workspace_id: str
) -> ExistingConfiguration:
    result = ExistingConfiguration()
    for row in conn.execute(
        "select executor_id from workspace_executor_allocations where workspace_id = ?",
        (workspace_id,),
    ).fetchall():
        result.allocations.add(row["executor_id"])
    for row in conn.execute(
        "select pipeline_key, node_key from workspace_node_bindings where workspace_id = ?",
        (workspace_id,),
    ).fetchall():
        result.bindings.add((row["pipeline_key"], row["node_key"]))
    for row in conn.execute(
        "select pipeline_key, node_key from workspace_node_limits where workspace_id = ?",
        (workspace_id,),
    ).fetchall():
        result.node_limits.add((row["pipeline_key"], row["node_key"]))
    return result
