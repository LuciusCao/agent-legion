"""Submit-campaign intake preflight (#532 PR-A, PR #541 round-2 P1).

The feeder (PR-C) feeds a submit campaign by handing each batch to
RunService.create_run; the campaign row therefore must not exist with items
that intake would refuse. This module is the read-only preflight of that
contract at creation/preview time — the SAME judgement the real intake runs
(``validate_run_item_types`` over the workspace's active revision) plus the
per-run item ceiling the batch path re-checks per slice. The heavier intake
work beyond this (node config freeze, code version pins) stays with the
feeder's actual run creation: the preflight only rules out targets that are
known-unfeedable when the campaign row is about to be written.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_item_types import validate_run_item_types
from server.app.workflows.definition import workflow_definition_from_dict


def preflight_submit_intake(
    job_db: Any, settings: Any, workspace_id: str, items: list[dict[str, Any]]
) -> None:
    """Reject items the workspace's run intake would refuse (read-only).

    无 active revision：create_run 会拒该 workspace 的每一个 item——campaign
    形态同样 fail-fast（不建 pending 行）。start-node 入口契约与
    workflows.max_items_per_run 与真实 intake 同判定（preview 与创建共享，
    避免把不可投递的 item 计成 would_create）。
    """
    active_revision = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    if active_revision is None:
        raise InvalidOperationError(
            "Workspace has no active workflow revision; publish a workflow revision first"
        )
    definition = workflow_definition_from_dict(json.loads(str(active_revision["definition_json"])))
    max_items = settings.executor_runtime.workflows.max_items_per_run
    if max_items and len(items) > max_items:
        raise InvalidOperationError(
            f"Campaign manifest has {len(items)} items, exceeding the per-run"
            f" limit {max_items} (workflows.max_items_per_run) — the feeder"
            " submits at most that many items per batch"
        )
    validate_run_item_types(definition, items)
