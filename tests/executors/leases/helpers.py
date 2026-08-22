from __future__ import annotations

import uuid

from server.app.executors.models import CODE_EXECUTOR_ID, LeaseClaimRequest
from server.app.jobs import JobQueries


def _setup_workspace(
    queries: JobQueries,
    name: str,
    executor_id: str = CODE_EXECUTOR_ID,
    workspace_limit: int | None = None,
    node_key: str = "review_keywords",
    local_limit: int | None = 1,
    workflow_key: str = "demo_workflow",
    node_keys: list[str] | None = None,
) -> tuple[str, str]:
    # executor_id/workspace_limit are legacy inert parameters (P-0.5): the
    # single implicit code pool needs no allocation/binding rows; only the
    # per-node limit insert survives.
    del executor_id, workspace_limit
    workspace = queries.create_workspace(name=name, default_workflow_key=workflow_key)
    workspace_id = workspace["id"]
    job_id = _create_job_in_workspace(
        queries,
        workspace_id,
        node_key=node_key,
        workflow_key=workflow_key,
        node_keys=node_keys,
    )
    if local_limit is not None:
        _set_node_limit(queries, workspace_id, workflow_key, node_key, local_limit)
    return workspace_id, job_id


def _create_job_in_workspace(
    queries: JobQueries,
    workspace_id: str,
    node_key: str = "review_keywords",
    workflow_key: str = "demo_workflow",
    node_keys: list[str] | None = None,
) -> str:
    job = queries.create_job(
        workflow_key=workflow_key,
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        run_id="",
        title="Test Job",
        node_keys=node_keys or [node_key],
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _set_node_limit(
    queries: JobQueries,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    concurrency_limit: int,
) -> None:
    with queries.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit)
            values (%s, %s, %s, %s)
            on conflict(workspace_id, workflow_key, node_key) do update set
              concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, workflow_key, node_key, concurrency_limit),
        )


def _claim_request(
    workspace_id: str,
    job_id: str,
    node_key: str = "review_keywords",
    executor_id: str = CODE_EXECUTOR_ID,
    global_capacity: int = 2,
    local_node_limit: int | None = 1,
    ttl: int = 60,
    log_path: str = "logs/run.log",
    workflow_key: str = "demo_workflow",
    execution_mode: str = "full",
    target_node_key: str | None = None,
    allowed_node_keys: tuple[str, ...] = (),
    shard_index: int | None = None,
) -> LeaseClaimRequest:
    return LeaseClaimRequest(
        executor_id=executor_id,
        global_capacity=global_capacity,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key=workflow_key,
        node_key=node_key,
        capability="review_keywords",
        local_node_limit=local_node_limit,
        lease_ttl_seconds=ttl,
        log_path=log_path,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        target_node_key=target_node_key,
        allowed_node_keys=allowed_node_keys,
        shard_index=shard_index,
    )
