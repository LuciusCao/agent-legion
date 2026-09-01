"""Refresh the DB skill lock: re-resolve every pinned ref to its commit.

The skill lock lives in ``global_settings`` under the ``skill_lock`` key
(retired ``config/skills.lock``). Since #322 there is no source registry:
this command iterates the lock's existing entries and re-resolves every
pinned ref (each skill may pin several, issue #76) against the in-place
repo at ``<skills root>/<key>``, then rewrites the lock document. Refs
nobody pinned are not touched, and ``latest`` never enters the lock.

CLI: ``uv run python -m server.app.skills.lock [--database-url DSN]
[--base-dir PATH] [--runs-dir PATH]`` — the DSN and runs dir default to the
configured database / skills scratch dir (``AGENT_LEGION_DATABASE_URL`` /
``AGENT_LEGION_SKILLS_RUNS_DIR`` / project settings).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from server.app.services.skill_lock_store import SkillLockStore
from server.app.settings import load_settings
from server.app.skills.config import LockedSkill, SkillsLock
from server.app.skills.manager import SkillManager, SkillStore
from server.app.skills.refresh import refresh_pinned_refs
from server.app.skills.skill_roots import default_skill_base_dir


def refresh_lock(store: SkillStore, base_dir: Path, runs_dir: Path | None = None) -> None:
    """Re-resolve every ref already pinned in the lock and rewrite the document."""
    manager = SkillManager(store=store, base_dir=base_dir, runs_dir=runs_dir)
    existing = store.get_lock() or SkillsLock()
    refreshed = {}
    for skill_key in sorted(existing.skills):
        workflow, capability = manager._parse_skill_key(skill_key)
        cache_dir = manager._resolve_cache_dir(workflow, capability)
        with manager._cache_lock_for(cache_dir):
            refs = refresh_pinned_refs(
                manager, skill_key, cache_dir, existing.skills[skill_key].refs
            )
            # repo is audit-only since #322 (the location derives from
            # skill_roots + key); record the canonical in-place path.
            refreshed[skill_key] = LockedSkill(repo=str(cache_dir), refs=refs)
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
    refresh_lock(SkillLockStore(dsn), args.base_dir, runs_dir=runs_dir)


if __name__ == "__main__":
    main()
