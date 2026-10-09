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

from server.app.agent_broker import manifest_guard
from server.app.agent_broker.claim_node_limit import node_limit_audit_value
from server.app.db.transaction import write_transaction
from server.app.executors._lease_control import lock_job_mutation_and_read_generation
from server.app.services.agent_node_profile_types import (
    PROFILE_SOURCE_NODE,
)

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker, AgentExecutionRequest

_ACTIVE_LEASE_CONSTRAINT = "idx_agent_requests_one_active_node"


def enqueue_request(broker: AgentExecutionBroker, request: AgentExecutionRequest) -> str | None:
    """Insert one queued request; None when the node already has an active one
    or the request's expected generation is stale (EXEC-GENERATION-001, #645
    review P1 — callers treat both with the existing skip semantics: the node
    stays pending and the next dispatch pass re-enqueues on the fresh epoch)."""
    # Fail fast on unroutable manifests (placeholder/empty model): they
    # would otherwise poison the queue head forever (issue #13). #843 评审
    # P1：保留成员名碰撞同点拒（节点失败点名冲突名）。
    manifest_guard.require_routable_execution(request.manifest)
    manifest_guard.require_unreserved_output_names(request.manifest)
    execution_id = request.execution_id or str(uuid.uuid4())
    try:
        with write_transaction(broker.database_dsn) as conn:
            # EXEC-GENERATION-001：打包（dispatch 按代次 N 评估）与 INSERT 之间
            # 可能夹着一次 rerun/upgrade 提交（代次 N+1）。入队事务先取
            # job-mutation 锁与 mutation 侧互斥再复核代次——不等即不插入；
            # 否则无人 claim 的 stale queued 行（如远端 Worker 离线）会把
            # has_active_request 的代次闸门外重派无限期挡住。enqueue 不持任何
            # 池级锁，直接取 job-mutation，全局锁序（池锁 → job-mutation →
            # 行锁）保持无环。
            current_generation = lock_job_mutation_and_read_generation(conn, request.job_id)
            if current_generation is None or current_generation != request.execution_generation:
                return None
            # Code requests are executor-routed (not Agent-routed) and carry
            # no versioned Agent definition; dispatch validated the binding,
            # code hash and worker eligibility already.
            # Self-contained agent nodes (profile_source='node', #933) have no
            # route row and no versioned definition: the frozen profile rides
            # the request row itself, so only the audit limit is read.
            # #1149: the code branch's node_concurrency_limit is an audit
            # snapshot of the workspace_node_limits row (1 = no configured
            # limit), same audit-only discipline as the agent branches — the
            # authoritative node-limit check is the claim transaction
            # (claim_node_limit, called from claim_evaluate; current value
            # per claim).
            if request.kind == "code":
                stored_limit = node_limit_audit_value(conn, request.workspace_id, request.node_key)
            elif request.profile_source == PROFILE_SOURCE_NODE:
                stored_limit = _workspace_agent_limit(conn, request.workspace_id)
            else:
                stored_limit = _validate_agent_route(conn, request)
            conn.execute(
                """
                insert into agent_execution_requests(
                  execution_id, workspace_id, job_id, node_key,
                  kind, agent_id, agent_definition_hash, node_concurrency_limit,
                  queued_at, manifest_json, pinned_agent_version, execution_generation,
                  profile_source, runtime, requires_labels_json
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, current_timestamp, %s, %s, %s,
                          %s, %s, %s)
                """,
                (
                    execution_id,
                    request.workspace_id,
                    request.job_id,
                    request.node_key,
                    request.kind,
                    request.agent_id,
                    request.agent_definition_hash,
                    stored_limit,
                    # INV-9（#876 P2-a）：本路径序语义已在冻结点归一化
                    # （stage_agent_inputs 按归一化名去重），sort_keys 只
                    # 做 canonical 形态，不再承载语义。
                    json.dumps(dict(request.manifest), ensure_ascii=False, sort_keys=True),
                    request.pinned_agent_version,
                    request.execution_generation,
                    request.profile_source,
                    request.runtime,
                    (
                        json.dumps(dict(request.requires_labels), sort_keys=True)
                        if request.requires_labels is not None
                        else None
                    ),
                ),
            )
    except IntegrityError as exc:
        # Only the one-active-request-per-node unique index means "already
        # enqueued". Anything else (FK violations, other constraints) is a
        # real error and must surface.
        constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
        if getattr(exc, "sqlstate", None) == "23505" and constraint == _ACTIVE_LEASE_CONSTRAINT:
            return None
        raise
    return execution_id


def _validate_agent_route(conn: Any, request: AgentExecutionRequest) -> int:
    """Re-validate the Agent route and definition pin; return the audit limit."""
    # #211 Phase 3 (read-layer binding): the route predicate keys on
    # (workspace_id, node_key) — workflow_key equals the workspace id on
    # every row (v62 binding, aligned by v68).
    route = conn.execute(
        """
        select target_kind, target_id from workspace_node_routes
        where workspace_id=%s and node_key=%s
        """,
        (request.workspace_id, request.node_key),
    ).fetchone()
    # No row at all: the active revision made the node self-contained
    # (#933) while this job's frozen snapshot still dispatches it through its
    # Agent definition — the definition-hash checks below stay authoritative.
    # A row that routes elsewhere is still a route change.
    if route is not None and route["target_kind"] != "agent":
        raise ValueError("workspace node is not routed to an Agent")
    if route is not None and route["target_id"] != request.agent_id:
        raise ValueError("workspace node Agent route changed before enqueue")
    if request.pinned_agent_version is not None:
        # Quality replay: the pin matches one immutable version row
        # (any status — archived/draft replays are the use case).
        definition = conn.execute(
            "select definition_hash from versioned_entities"
            " where entity_type='agent' and workspace_id=%s"
            " and entity_key=%s and version=%s",
            (request.workspace_id, request.agent_id, request.pinned_agent_version),
        ).fetchone()
        if definition is None or definition["definition_hash"] != request.agent_definition_hash:
            raise ValueError("pinned Agent version is unavailable or changed before enqueue")
    else:
        definition = conn.execute(
            "select definition_hash from versioned_entities"
            " where entity_type='agent' and workspace_id=%s"
            " and entity_key=%s and status='published'",
            (request.workspace_id, request.agent_id),
        ).fetchone()
        if definition is None or definition["definition_hash"] != request.agent_definition_hash:
            raise ValueError("Agent definition is unavailable or changed before enqueue")
    return _workspace_agent_limit(conn, request.workspace_id)


def _workspace_agent_limit(conn: Any, workspace_id: str) -> int:
    """Audit-only snapshot of the governing workspace-level limit at enqueue.

    1 records "no configured limit (unlimited)". Never enforced.
    """
    capacity = conn.execute(
        "select max_concurrency from workspace_agent_capacities where workspace_id=%s",
        (workspace_id,),
    ).fetchone()
    return int(capacity["max_concurrency"]) if capacity is not None else 1
