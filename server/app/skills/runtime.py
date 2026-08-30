"""Skill runtime helpers: manager assembly + execution-time resolution.

Moved from ``executors/_pi_skill.py`` (P-0.5): these serve the Agent runtime
dispatch path, not any executor adapter.
"""

from __future__ import annotations

import time
from pathlib import Path

from server.app.db.dialect import ConnectSource
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.manager import SkillManager
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.skill_version import resolve_skill_version
from server.app.workflows.skills import resolve_workflow_skill

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


def resolve_skill_dir(
    skill_manager: SkillManager,
    skill: str,
    execution_id: str,
) -> Path:
    """Resolve a skill to a validated, execution-private directory."""
    skill_dir = skill_manager.get_skill_dir(skill, execution_id)
    try:
        resolve_workflow_skill(skill_manager.base_dir, skill)
        return skill_dir
    except Exception:
        # #204 broad-except audit: cleanup-guard-then-bare-re-raise (#233
        # pattern — clean up broad, classify never). get_skill_dir above
        # already copytree'd the execution-private run dir; whatever made
        # the contract validation fail (ValueError for the documented
        # missing/escaping-skill cases, OSError from the filesystem, or a
        # programming error), the private dir must be reclaimed before the
        # exception propagates or every retry leaks one runs/<execution_id>
        # copy (only the age-based sweeper would reclaim it). The bare
        # ``raise`` preserves the original type — the callers
        # (output_validation, the dispatch path) classify it themselves.
        skill_manager.cleanup_execution(execution_id)
        raise


def build_skill_manager(database_dsn: ConnectSource, runs_dir: Path | None = None) -> SkillManager:
    """Project-standard SkillManager: DB-backed sources, shared user-level base dir.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187).

    ``runs_dir`` must come from ``settings.skills_runs_dir`` so every process
    sharing the skill cache (server, lock-refresh CLI) resolves the same
    lock domain; None falls back to the deterministic temp default.
    """
    return SkillManager(
        store=SkillSourceStore(database_dsn),
        base_dir=default_skill_base_dir(),
        runs_dir=runs_dir,
    )
