from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.executors.config import PiCapabilityConfig
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.manager import SkillManager
from server.app.workflows.skill_version import resolve_skill_version
from server.app.workflows.skills import resolve_workflow_skill

logger = logging.getLogger(__name__)


# Skill version strings for manifests, memoized briefly per cache dir: the
# probes (rev-parse + describe) fork git on every call, and the value only
# changes on relock — which the manager's doc cache already surfaces with
# the same TTL-class delay (SkillDocCache, 5s).
_version_memo: dict[str, tuple[float, str]] = {}


def get_skill_version(skill_manager: SkillManager, skill: str) -> str:
    key = str(skill_manager.base_dir / skill)
    cached = _version_memo.get(key)
    if cached is not None and time.monotonic() - cached[0] < 5.0:
        return cached[1]
    version = resolve_skill_version(skill_manager.base_dir / skill)
    _version_memo[key] = (time.monotonic(), version)
    return version


def prepare_execution(
    cancelled: set[str],
    capabilities: Mapping[str, PiCapabilityConfig],
    context: ExecutionContext,
) -> tuple[PiCapabilityConfig | None, ExecutionResult | None]:
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
    try:
        resolve_workflow_skill(skill_manager.base_dir, skill)
        return skill_dir
    except Exception:
        skill_manager.cleanup_execution(execution_id)
        raise


def build_skill_manager(database_dsn: DatabaseDsn) -> SkillManager:
    """Project-standard SkillManager: DB-backed sources, shared user-level base dir."""
    return SkillManager(
        store=SkillSourceStore(database_dsn),
        base_dir=Path.home() / ".agents" / "skills" / "agent-legion",
    )
