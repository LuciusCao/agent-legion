"""Enqueue transaction for the Agent execution queue.

Split out of ``broker.py`` so the broker module only carries the queue
protocol; mirrors the ``claim.py``/``release.py``/``sweepers.py`` layout.
Functions take the broker instance as their first argument.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from psycopg import IntegrityError

from server.app.agent_broker.manifest_guard import require_routable_execution
from server.app.db.transaction import write_transaction

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker, AgentExecutionRequest

_ACTIVE_LEASE_CONSTRAINT = "idx_agent_requests_one_active_node"


def enqueue_request(broker: AgentExecutionBroker, request: AgentExecutionRequest) -> str | None:
    """Insert one queued request; None when the node already has an active one."""
    # Fail fast on unroutable manifests (placeholder/empty model): they
    # would otherwise poison the queue head forever (issue #13).
    require_routable_execution(request.manifest)
    execution_id = request.execution_id or str(uuid.uuid4())
    try:
        with write_transaction(broker.database_dsn) as conn:
            # Code requests are executor-routed (not Agent-routed) and carry
            # no versioned Agent definition; dispatch validated the binding,
            # code hash and worker eligibility already.
            stored_limit = 1 if request.kind == "code" else _validate_agent_route(conn, request)
            conn.execute(
                """
                insert into agent_execution_requests(
                  execution_id, workspace_id, job_id, workflow_key, node_key,
                  kind, agent_id, agent_definition_hash, node_concurrency_limit,
                  queued_at, manifest_json, pinned_agent_version
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, current_timestamp, %s, %s)
                """,
                (
                    execution_id,
                    request.workspace_id,
                    request.job_id,
                    request.workflow_key,
                    request.node_key,
                    request.kind,
                    request.agent_id,
                    request.agent_definition_hash,
                    stored_limit,
                    json.dumps(dict(request.manifest), ensure_ascii=False, sort_keys=True),
                    request.pinned_agent_version,
                ),
            )
    except IntegrityError as exc:
        # Only the one-active-request-per-node unique index means "already
        # enqueued". Anything else (FK violations, other constraints) is a
        # real error and must surface.
        diag = getattr(exc, "diag", None)
        constraint = getattr(diag, "constraint_name", None) if diag is not None else None
        if getattr(exc, "sqlstate", None) == "23505" and constraint == _ACTIVE_LEASE_CONSTRAINT:
            return None
        raise
    return execution_id


def _validate_agent_route(conn: Any, request: AgentExecutionRequest) -> int:
    """Re-validate the Agent route and definition pin; return the audit limit."""
    route = conn.execute(
        """
        select target_kind, target_id from workspace_node_routes
        where workspace_id=%s and workflow_key=%s and node_key=%s
        """,
        (request.workspace_id, request.workflow_key, request.node_key),
    ).fetchone()
    if route is None or route["target_kind"] != "agent":
        raise ValueError("workspace node is not routed to an Agent")
    if route["target_id"] != request.agent_id:
        raise ValueError("workspace node Agent route changed before enqueue")
    if request.pinned_agent_version is not None:
        # Quality replay: the pin matches one immutable version row
        # (any status — archived/draft replays are the use case).
        definition = conn.execute(
            "select definition_hash from versioned_entities"
            " where entity_type='agent' and workspace_id is null"
            " and entity_key=%s and version=%s",
            (request.agent_id, request.pinned_agent_version),
        ).fetchone()
        if definition is None or definition["definition_hash"] != request.agent_definition_hash:
            raise ValueError("pinned Agent version is unavailable or changed before enqueue")
    else:
        definition = conn.execute(
            "select definition_hash from versioned_entities"
            " where entity_type='agent' and workspace_id is null"
            " and entity_key=%s and status='published'",
            (request.agent_id,),
        ).fetchone()
        if definition is None or definition["definition_hash"] != request.agent_definition_hash:
            raise ValueError("Agent definition is unavailable or changed before enqueue")
    capacity = conn.execute(
        "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
        (request.workspace_id,),
    ).fetchone()
    # Audit-only snapshot of the governing workspace-level limit at enqueue
    # time; 1 records "no configured limit (unlimited)". Never enforced.
    return int(capacity["max_concurrency"]) if capacity is not None else 1
