"""Stockpile snapshot loading: queue/done aggregation, fleet capacity, tiers.

Split out of ``agent_stock`` so that module only carries the gate math for
the file-size budget. The workflow-tier derivation parses revision
snapshots into per-bucket DAG depths; ``agent_stock`` consumes the result
as the shared capacity-floor priorities.
"""

from __future__ import annotations

import json

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection
from server.app.workflow_worker.agent_stock import (
    UNKNOWN_PRIORITY,
    AgentStockConfig,
    StockBucket,
    StockSnapshot,
)
from server.app.workflows.definition import workflow_definition_from_dict

# Workflow-tier rows: which DAG node each live-or-recent request serves.
# One branch per matching state so each hits its partial index
# (idx_agent_requests_queued_head / _done_recent / _cancelled_recent): a
# single `state='queued' or finished_at > ...` predicate defeats all of them
# and seq-scans the whole table on every stock pass (same failure mode the
# reaper split fixed). finished_at is only written alongside a terminal
# state, so the done/cancelled branches cover the recency predicate exactly.
# Kept as a module constant so tests can pin the exact plan of the string
# that production runs.
TIER_ROWS_SQL = (
    "select distinct r.workspace_id, r.agent_id, r.node_key, wr.definition_json from ("
    " select workspace_id, agent_id, node_key, job_id from agent_execution_requests"
    " where state = 'queued' union all"
    " select workspace_id, agent_id, node_key, job_id from agent_execution_requests"
    " where state = 'done' and finished_at > now() - make_interval(secs => %s) union all"
    " select workspace_id, agent_id, node_key, job_id from agent_execution_requests"
    " where state = 'cancelled' and finished_at > now() - make_interval(secs => %s)"
    " ) r join jobs j on j.id = r.job_id"
    " join workflow_revisions wr on wr.id = j.workflow_revision_id"
)


def _node_depths(definition_json: str) -> dict[str, int]:
    """Topological depth per node key (0 = no upstream) from a revision snapshot.

    Unparseable/legacy definitions degrade to an empty map: their buckets
    fall back to the unknown (lowest) priority tier instead of failing the
    whole stock pass.
    """
    try:
        definition = workflow_definition_from_dict(json.loads(definition_json))
    except (ValueError, TypeError):
        return {}
    upstream: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        if edge.target in upstream:
            upstream[edge.target].append(edge.source)
    depths: dict[str, int] = {}

    def depth(node_key: str) -> int:
        if node_key not in depths:
            depths[node_key] = 0  # guard against cycles (definitions are validated acyclic)
            depths[node_key] = 1 + max((depth(dep) for dep in upstream[node_key]), default=-1)
        return depths[node_key]

    for node_key in upstream:
        depth(node_key)
    return depths


def load_stock_snapshot(dsn: DatabaseDsn, config: AgentStockConfig) -> StockSnapshot:
    """Aggregate queued counts, the recent done rate, the live fleet capacity,
    and the per-bucket workflow tiers for the shared capacity floor."""
    queued: dict[tuple[str, str], int] = {}
    done: dict[tuple[str, str], int] = {}
    priorities: dict[tuple[str, str], int] = {}
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select workspace_id, agent_id, count(*) as n"
            " from agent_execution_requests where state = 'queued'"
            " group by 1, 2"
        ).fetchall()
        for row in rows:
            queued[(str(row["workspace_id"]), str(row["agent_id"]))] = int(row["n"])
        rows = conn.execute(
            "select workspace_id, agent_id, count(*) as n"
            " from agent_execution_requests"
            " where state = 'done' and finished_at > now() - make_interval(secs => %s)"
            " group by 1, 2",
            (config.window_seconds,),
        ).fetchall()
        for row in rows:
            done[(str(row["workspace_id"]), str(row["agent_id"]))] = int(row["n"])
        capacity_row = conn.execute(
            "select coalesce(sum(max_concurrency), 0) as capacity from agent_workers"
            " where revoked_at is null"
            " and last_seen_at > now() - make_interval(secs => %s)",
            (config.worker_fresh_seconds,),
        ).fetchone()
        capacity = int(capacity_row["capacity"]) if capacity_row is not None else 0
        # Workflow tier per bucket: which DAG node (hence how deep) each
        # (workspace, agent) pair serves. Only live or recent requests are
        # relevant to pool contention; jobs without a revision snapshot are
        # skipped and their buckets stay in the unknown tier.
        rows = conn.execute(
            TIER_ROWS_SQL,
            (config.window_seconds, config.window_seconds),
        ).fetchall()
    depth_cache: dict[str, dict[str, int]] = {}
    for row in rows:
        definition_json = str(row["definition_json"])
        if definition_json not in depth_cache:
            depth_cache[definition_json] = _node_depths(definition_json)
        tier = depth_cache[definition_json].get(str(row["node_key"]))
        if tier is None:
            continue
        pair = (str(row["workspace_id"]), str(row["agent_id"]))
        if tier > priorities.get(pair, UNKNOWN_PRIORITY):
            priorities[pair] = tier
    return StockSnapshot(
        config=config,
        capacity=capacity,
        buckets={
            key: StockBucket(
                queued=queued.get(key, 0),
                done_in_window=done.get(key, 0),
            )
            for key in queued.keys() | done.keys()
        },
        priorities=priorities,
    )
