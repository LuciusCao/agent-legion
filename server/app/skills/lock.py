"""Refresh the DB skill lock: resolve every declared source to its commit.

The skill lock lives in ``global_settings`` under the ``skill_lock`` key
(retired ``config/skills.lock``); this command re-resolves every skill
declared in the ``skill_sources`` document against its local/remote repo and
rewrites the lock document.

CLI: ``uv run python -m server.app.skills.lock [--database-url DSN]
[--base-dir PATH] [--runs-dir PATH]`` — the DSN and runs dir default to the
configured database / skills scratch dir (``AGENT_LEGION_DATABASE_URL`` /
``AGENT_LEGION_SKILLS_RUNS_DIR`` / project settings).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from server.app.services.skill_source_store import SkillSourceStore
from server.app.settings import load_settings
from server.app.skills.config import SkillsLock
from server.app.skills.manager import SkillManager, SkillStore
from server.app.skills.refresh import refresh_source
from server.app.skills.skill_roots import default_skill_base_dir


def refresh_lock(store: SkillStore, base_dir: Path, runs_dir: Path | None = None) -> None:
    """Resolve every declared skill to its current commit and write the DB lock."""
    manager = SkillManager(store=store, base_dir=base_dir, runs_dir=runs_dir)
    config = manager._load_config()
    refreshed = {}
    for skill_key in config.skills:
        workflow, capability = manager._parse_skill_key(skill_key)
        cache_dir = manager._resolve_cache_dir(workflow, capability)
        with manager._cache_lock_for(cache_dir):
            refreshed[skill_key] = refresh_source(
                manager, skill_key, config.skills[skill_key], cache_dir
            )
    with manager._lock_write_lock:
        manager._write_lock_unlocked(SkillsLock(skills=refreshed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the DB skill lock")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN override")
    parser.add_argument(
        "--base-dir",
        default=str(default_skill_base_dir()),
        type=Path,
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        type=Path,
        help="Skills runs/scratch dir override (default: settings, then temp dir)",
    )
    args = parser.parse_args()
    settings = load_settings()
    dsn = args.database_url or settings.database_url
    runs_dir = args.runs_dir or settings.skills_runs_dir
    refresh_lock(SkillSourceStore(dsn), args.base_dir, runs_dir=runs_dir)


if __name__ == "__main__":
    main()
