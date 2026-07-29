from __future__ import annotations

import uuid

from server.app.executors.models import LeaseClaimRequest
from server.app.jobs import JobQueries


def _setup_workspace(
    queries: JobQueries,
    name: str,
    executor_id: str,
    workspace_limit: int,
    node_key: str = "review_keywords",
    local_limit: int | None = 1,
    workflow_key: str = "question_comprehension_info",
    node_keys: list[str] | None = None,
) -> tuple[str, str]:
    workspace = queries.create_workspace(name=name, default_workflow_key=workflow_key)
    workspace_id = workspace["id"]
    job_id = _create_job_in_workspace(
        queries,
        workspace_id,
        node_key=node_key,
        workflow_key=workflow_key,
        node_keys=node_keys,
    )
    _bind_executor_to_node(
        queries,
        workspace_id,
        executor_id,
        workspace_limit,
        node_key=node_key,
        local_limit=local_limit,
        workflow_key=workflow_key,
    )
    return workspace_id, job_id


def _create_job_in_workspace(
    queries: JobQueries,
    workspace_id: str,
    node_key: str = "review_keywords",
    workflow_key: str = "question_comprehension_info",
    node_keys: list[str] | None = None,
) -> str:
    job = queries.create_job(
        workflow_key=workflow_key,
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        batch_id="",
        title="Test Job",
        node_keys=node_keys or [node_key],
        workspace_id=workspace_id,
    )
    return str(job["id"])


def _bind_executor_to_node(
    queries: JobQueries,
    workspace_id: str,
    executor_id: str,
    workspace_limit: int,
    node_key: str = "review_keywords",
    local_limit: int | None = 1,
    workflow_key: str = "question_comprehension_info",
) -> None:
    with queries.connect() as conn:
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            values (%s, %s, %s)
            on conflict(workspace_id, executor_id) do update set
              concurrency_limit=excluded.concurrency_limit
            """,
            (workspace_id, executor_id, workspace_limit),
        )
        conn.execute(
            """
            insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)
            values (%s, %s, %s, %s)
            on conflict(workspace_id, workflow_key, node_key) do update set
              executor_id=excluded.executor_id
            """,
            (workspace_id, workflow_key, node_key, executor_id),
        )
        if local_limit is not None:
            conn.execute(
                """
                insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit)
                values (%s, %s, %s, %s)
                on conflict(workspace_id, workflow_key, node_key) do update set
                  concurrency_limit=excluded.concurrency_limit
                """,
                (workspace_id, workflow_key, node_key, local_limit),
            )


def _claim_request(
    workspace_id: str,
    job_id: str,
    node_key: str = "review_keywords",
    executor_id: str = "code-default",
    global_capacity: int = 2,
    local_node_limit: int | None = 1,
    ttl: int = 60,
    log_path: str = "logs/run.log",
    workflow_key: str = "question_comprehension_info",
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
