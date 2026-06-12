from __future__ import annotations

import json
import logging
from typing import Any

from server.app.executors.config import ExecutorConfig
from server.app.jobs import executor_configuration
from server.app.jobs.queries import JobQueries
from server.app.pipelines.definition import PipelineDefinition

logger = logging.getLogger(__name__)


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


_DEFAULT_LOCAL_EXECUTOR_ID = "local-default"
_DEFAULT_PI_EXECUTOR_ID = "pi-default"


def bootstrap_workspace_executor_defaults(
    job_db: JobQueries,
    definitions: list[PipelineDefinition],
    executors: dict[str, ExecutorConfig],
) -> None:
    definitions_by_key = {definition.key: definition for definition in definitions}
    with job_db.connect() as conn:
        workspaces = conn.execute(
            "select id, default_pipeline_key, pipeline_config_json from workspaces"
        ).fetchall()
        for workspace_row in workspaces:
            workspace_id = workspace_row["id"]
            if executor_configuration.workspace_executor_configuration_is_authoritative(
                conn, workspace_id
            ):
                continue
            pipeline_key = workspace_row["default_pipeline_key"]
            pipeline_config = _decode_json_object(workspace_row["pipeline_config_json"])

            definition = definitions_by_key.get(pipeline_key)
            if definition is None:
                logger.warning(
                    "Workspace %s default pipeline %s has no definition; skipping bootstrap",
                    workspace_id,
                    pipeline_key,
                )
                continue

            has_local_nodes = any(node.runner == "local" for node in definition.nodes.values())
            agent_assignments = conn.execute(
                "select agent_id, concurrency_limit from workspace_agent_assignments "
                "where workspace_id = ?",
                (workspace_id,),
            ).fetchall()
            pi_assignment = next(
                (row for row in agent_assignments if row["agent_id"] == "pi"), None
            )
            has_pi_assignment = pi_assignment is not None
            pi_limit = int(pi_assignment["concurrency_limit"]) if pi_assignment else 0

            if has_local_nodes and _DEFAULT_LOCAL_EXECUTOR_ID not in executors:
                raise RuntimeError(
                    f"Workspace {workspace_id} requires {_DEFAULT_LOCAL_EXECUTOR_ID} executor"
                )
            if has_pi_assignment and _DEFAULT_PI_EXECUTOR_ID not in executors:
                raise RuntimeError(
                    f"Workspace {workspace_id} requires {_DEFAULT_PI_EXECUTOR_ID} executor"
                )

            local_limit = pipeline_config.get("local")
            if not isinstance(local_limit, int) or isinstance(local_limit, bool) or local_limit < 1:
                local_limit = definition.concurrency.local

            if has_local_nodes:
                conn.execute(
                    """
                    insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit)
                    values (?, ?, ?)
                    on conflict(workspace_id, executor_id) do nothing
                    """,
                    (workspace_id, _DEFAULT_LOCAL_EXECUTOR_ID, local_limit),
                )

            if has_pi_assignment:
                conn.execute(
                    """
                    insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit)
                    values (?, ?, ?)
                    on conflict(workspace_id, executor_id) do nothing
                    """,
                    (workspace_id, _DEFAULT_PI_EXECUTOR_ID, pi_limit),
                )

            for node in definition.nodes.values():
                if node.runner == "local":
                    conn.execute(
                        """
                        insert into workspace_node_bindings (workspace_id, pipeline_key, node_key, executor_id)
                        values (?, ?, ?, ?)
                        on conflict(workspace_id, pipeline_key, node_key) do nothing
                        """,
                        (workspace_id, pipeline_key, node.key, _DEFAULT_LOCAL_EXECUTOR_ID),
                    )

                    node_limit = pipeline_config.get("nodes", {}).get(node.key)
                    if (
                        not isinstance(node_limit, int)
                        or isinstance(node_limit, bool)
                        or node_limit < 1
                    ):
                        node_limit = definition.concurrency.nodes.get(node.key)

                    if (
                        isinstance(node_limit, int)
                        and not isinstance(node_limit, bool)
                        and node_limit >= 1
                    ):
                        conn.execute(
                            """
                            insert into workspace_node_limits (workspace_id, pipeline_key, node_key, concurrency_limit)
                            values (?, ?, ?, ?)
                            on conflict(workspace_id, pipeline_key, node_key) do nothing
                            """,
                            (workspace_id, pipeline_key, node.key, node_limit),
                        )
                elif node.agent is not None and node.agent.engine == "pi":
                    if has_pi_assignment:
                        conn.execute(
                            """
                            insert into workspace_node_bindings (
                                workspace_id, pipeline_key, node_key, executor_id
                            )
                            values (?, ?, ?, ?)
                            on conflict(workspace_id, pipeline_key, node_key) do nothing
                            """,
                            (workspace_id, pipeline_key, node.key, _DEFAULT_PI_EXECUTOR_ID),
                        )
                    else:
                        logger.warning(
                            "Workspace %s pipeline %s node %s has no Pi allocation; "
                            "skipping binding",
                            workspace_id,
                            pipeline_key,
                            node.key,
                        )
                else:
                    logger.warning(
                        "Workspace %s pipeline %s node %s uses unknown agent engine %s; skipping binding",
                        workspace_id,
                        pipeline_key,
                        node.key,
                        node.agent.engine if node.agent else "none",
                    )
