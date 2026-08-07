"""Refresh the DB skill lock: resolve every declared source to its commit.

The skill lock lives in ``global_settings`` under the ``skill_lock`` key
(retired ``config/skills.lock``); this command re-resolves every skill
declared in the ``skill_sources`` document against its local/remote repo and
rewrites the lock document.

CLI: ``uv run python -m server.app.skills.lock [--database-url DSN]
[--base-dir PATH]`` — the DSN defaults to the configured database
(``AGENT_LEGION_DATABASE_URL`` / project settings).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsLock
from server.app.skills.manager import SkillManager, SkillStore


def refresh_lock(store: SkillStore, base_dir: Path) -> None:
    """Resolve every declared skill to its current commit and write the DB lock."""
    manager = SkillManager(store=store, base_dir=base_dir)
    config = manager._load_config()
    refreshed = {}
    for skill_key in config.skills:
        workflow, capability = manager._parse_skill_key(skill_key)
        cache_dir = manager._resolve_cache_dir(workflow, capability)
        with manager._cache_lock_for(cache_dir):
            refreshed[skill_key] = manager._refresh_source(
                skill_key, config.skills[skill_key], cache_dir
            )
    with manager._lock_write_lock:
        manager._write_lock_unlocked(SkillsLock(skills=refreshed))


def _default_dsn() -> str:
    from server.app.settings import load_settings

    return load_settings().database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the DB skill lock")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN override")
    parser.add_argument(
        "--base-dir",
        default=str(Path.home() / ".agents" / "skills" / "agent-legion"),
        type=Path,
    )
    args = parser.parse_args()
    dsn = args.database_url or _default_dsn()
    refresh_lock(SkillSourceStore(dsn), args.base_dir)


if __name__ == "__main__":
    main()
