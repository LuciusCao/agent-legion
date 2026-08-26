"""Shared SQL seeding helpers.

Local ``_insert_*``/``_seed_*`` copies of these statements had drifted across
test files (and schema changes had to be repeated in dozens of places); route
them through this module instead.
"""

from __future__ import annotations

from datetime import datetime


def insert_workspace(
    conn, *, workspace_id: str, name: str = "Test", default_workflow_key: str = "demo_workflow"
) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, %s)"
        " on conflict (id) do nothing",
        (workspace_id, name, default_workflow_key),
    )


def insert_job(
    conn,
    *,
    job_id: str,
    workspace_id: str,
    workflow_key: str = "questions",
    source_type: str = "question",
    source_id: str | None = None,
) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
        " values (%s, %s, %s, %s, %s) on conflict (id) do nothing",
        (job_id, workspace_id, workflow_key, source_type, source_id or job_id),
    )


def insert_node_run(
    conn, *, run_id: int, job_id: str, node_key: str = "generate", status: str = "completed"
) -> None:
    conn.execute(
        "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)"
        " on conflict (id) do update set job_id=excluded.job_id,"
        " node_key=excluded.node_key, status=excluded.status",
        (run_id, job_id, node_key, status),
    )


def insert_token_usage(
    conn,
    *,
    node_run_id: int,
    job_id: str,
    workspace_id: str,
    node_key: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    skill_version: str = "",
    created_at: datetime | None = None,
    message_count: int = 1,
) -> None:
    """Upsert one node_run_token_usage row.

    ``skill_version`` is NOT NULL with a '' default, so an explicit None would
    bypass the column default and violate the constraint; same for
    ``created_at`` (default current_timestamp), which is only set when the
    caller needs a specific bucket timestamp.
    """
    total = input_tokens + output_tokens + cache_read_tokens
    if created_at is None:
        conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (node_run_id) do update set
              job_id=excluded.job_id,
              workspace_id=excluded.workspace_id,
              node_key=excluded.node_key,
              provider=excluded.provider,
              model=excluded.model,
              skill_version=excluded.skill_version,
              message_count=excluded.message_count,
              input_tokens=excluded.input_tokens,
              output_tokens=excluded.output_tokens,
              cache_read_tokens=excluded.cache_read_tokens,
              total_tokens=excluded.total_tokens
            """,
            (
                node_run_id,
                job_id,
                workspace_id,
                node_key,
                provider,
                model,
                skill_version,
                message_count,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                total,
            ),
        )
    else:
        conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model, skill_version,
              message_count, input_tokens, output_tokens, cache_read_tokens, total_tokens,
              created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (node_run_id) do update set
              job_id=excluded.job_id,
              workspace_id=excluded.workspace_id,
              node_key=excluded.node_key,
              provider=excluded.provider,
              model=excluded.model,
              skill_version=excluded.skill_version,
              message_count=excluded.message_count,
              input_tokens=excluded.input_tokens,
              output_tokens=excluded.output_tokens,
              cache_read_tokens=excluded.cache_read_tokens,
              total_tokens=excluded.total_tokens
            """,
            (
                node_run_id,
                job_id,
                workspace_id,
                node_key,
                provider,
                model,
                skill_version,
                message_count,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                total,
                created_at,
            ),
        )
