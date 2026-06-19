from __future__ import annotations

import logging
from pathlib import Path

from server.app.executors.config import PiCapabilityConfig
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.skills.manager import SkillManager
from server.app.workflows.skills import resolve_workflow_skill

logger = logging.getLogger(__name__)


def prepare_execution(
    cancelled: set[str],
    capabilities: dict[str, PiCapabilityConfig],
    context: ExecutionContext,
) -> tuple[PiCapabilityConfig, ExecutionResult | None]:
    """Return the capability config, or an early result if the run cannot start."""
    if context.execution_id in cancelled:
        cancelled.discard(context.execution_id)
        return None, ExecutionResult(
            status="cancelled",
            exit_code=-1,
            error_message="execution was cancelled before starting",
            log_path=str(context.log_path),
        )

    capability_config = capabilities.get(context.capability)
    if capability_config is None:
        return None, ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=f"capability {context.capability!r} is not supported",
            log_path=str(context.log_path),
        )

    return capability_config, None


def resolve_skill_dir(
    skill_manager: SkillManager,
    skill: str,
    execution_id: str,
) -> Path:
    """Resolve a Pi skill to a validated, execution-private directory."""
    skill_dir = skill_manager.get_skill_dir(skill, execution_id)
    resolve_workflow_skill(skill_manager.base_dir, skill)
    return skill_dir
