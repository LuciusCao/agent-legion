"""Read-side filtering for workspace executor rows of retired Executor definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.executors.config import ExecutorConfig


def filter_known_executors(
    configuration: dict[str, list[dict[str, Any]]],
    executor_definitions: Mapping[str, ExecutorConfig] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Drop rows referencing Executors absent from the runtime definitions.

    Retired Executors (e.g. ``pi``) may still have rows in the workspace
    tables; echoing them back on PUT would fail validation, so the read side
    hides them. The next successful save physically removes them. ``None``
    definitions means the caller has no runtime config and reads unfiltered.
    """
    if executor_definitions is None:
        return configuration
    allocations = [
        row for row in configuration["allocations"] if row["executor_id"] in executor_definitions
    ]
    bindings = [
        row for row in configuration["bindings"] if row["executor_id"] in executor_definitions
    ]
    bound_nodes = {(row["workflow_key"], row["node_key"]) for row in bindings}
    node_limits = [
        row
        for row in configuration["node_limits"]
        if (row["workflow_key"], row["node_key"]) in bound_nodes
    ]
    return {"allocations": allocations, "bindings": bindings, "node_limits": node_limits}
