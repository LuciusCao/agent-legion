"""Entry contract check: item types vs the start node's accepted_item_types.

RunService.create_run runs this before the first write (D4): a workflow
whose start node does not accept an item type rejects the whole request with
InvalidOperationError (400 via the job_http mapping), leaving no run behind.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.schema import WorkflowDefinition


def validate_run_item_types(definition: WorkflowDefinition, items: list[dict[str, Any]]) -> None:
    """Reject items whose type the workflow's start node does not accept."""
    start = definition.start_node
    if start is None:
        return
    accepted = sorted(start.accepted_item_types)
    for item in items:
        if not isinstance(item, dict):
            # Same contract as _resolve_items; check shape before type so a
            # non-object item does not surface as "type None not accepted".
            raise InvalidOperationError("Each item must be an object")
        item_type = item.get("type")
        if item_type not in start.accepted_item_types:
            raise InvalidOperationError(
                f"Item type {item_type!r} is not accepted by this workflow "
                f"(accepted_item_types: {accepted})"
            )
