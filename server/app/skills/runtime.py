"""Skill runtime helpers: manager assembly.

Moved from ``executors/_pi_skill.py`` (P-0.5): these serve the Agent runtime
dispatch path, not any executor adapter. Execution-time, ref-aware checkouts
(issue #76) live in ``server.app.skills.checkout``.
"""

from __future__ import annotations

from pathlib import Path

from server.app.db.dialect import ConnectSource
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.manager import SkillManager
from server.app.skills.skill_roots import default_skill_base_dir


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
