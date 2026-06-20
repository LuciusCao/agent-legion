from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.jobs.queries import JobQueries
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.helpers import ensure_legacy_workspace_tables


def _sample_executors() -> dict[str, ExecutorConfig]:
    return {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=2,
            capabilities={
                "local_a": LocalCapabilityConfig(handler="reading_analysis.local_a"),
                "local_b": LocalCapabilityConfig(handler="reading_analysis.local_b"),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=8,
            capabilities={
                "pi_a": PiCapabilityConfig(
                    skill="reading_analysis/pi_a",
                    tools=("read", "write", "bash"),
                )
            },
        ),
    }


def _sample_workflows() -> list[WorkflowDefinition]:
    return [
        _sample_workflow(),
        _legacy_unconfigured_agent_workflow(),
        _question_comprehension_info_workflow(),
    ]


def _set_workflow_config(queries: JobQueries, workspace_id: str, config: dict[str, Any]) -> None:
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (json.dumps(config, ensure_ascii=False, sort_keys=True), workspace_id),
        )


def _insert_legacy_agent_assignment(
    queries: JobQueries, workspace_id: str, agent_id: str, concurrency_limit: int
) -> None:
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) "
            "values (?, ?, ?) on conflict(workspace_id, agent_id) do update set "
            "concurrency_limit = excluded.concurrency_limit",
            (workspace_id, agent_id, max(1, concurrency_limit)),
        )


def _list_legacy_agent_assignments(queries: JobQueries, workspace_id: str) -> list[dict[str, Any]]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select agent_id, concurrency_limit from workspace_agent_assignments "
            "where workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    return [{"agent_id": r["agent_id"], "concurrency_limit": r["concurrency_limit"]} for r in rows]


def _sample_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(
                key="local_a",
                label="Local A",
                capability="local_a",
            ),
            "local_b": WorkflowNode(
                key="local_b",
                label="Local B",
                capability="local_b",
            ),
            "pi_a": WorkflowNode(
                key="pi_a",
                label="Pi A",
                capability="pi_a",
            ),
        },
    )


def _legacy_unconfigured_agent_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="question_content",
        label="Question Content",
        intake=WorkflowIntake(),
        nodes={
            "fetch": WorkflowNode(
                key="fetch",
                label="Fetch",
                capability="local_a",
            ),
            "understand": WorkflowNode(
                key="understand",
                label="Understand",
                capability="understand",
            ),
        },
    )


def _question_comprehension_info_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="question_comprehension_info",
        label="Question Comprehension Info",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(
                key="local_a",
                label="Local A",
                capability="local_a",
            ),
        },
    )


def _fetch_all_allocations(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, executor_id, concurrency_limit "
            "from workspace_executor_allocations order by executor_id"
        ).fetchall()
        return [dict(row) for row in rows]


def _fetch_all_bindings(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, workflow_key, node_key, executor_id "
            "from workspace_node_bindings order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def _fetch_all_node_limits(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, workflow_key, node_key, concurrency_limit "
            "from workspace_node_limits order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def _table_exists(queries: JobQueries, table: str) -> bool:
    with queries._connect_read() as conn:
        row = conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()
        return row is not None


def _seed_default_workspace_assignment(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    queries.create_workspace("default", default_workflow_key="question_comprehension_info")
    _insert_legacy_agent_assignment(queries, "default", "pi", 3)
