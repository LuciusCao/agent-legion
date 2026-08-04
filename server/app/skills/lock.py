from __future__ import annotations

import argparse
from pathlib import Path

from server.app.skills.config import SkillsLock
from server.app.skills.manager import SkillManager


def refresh_lock(config_path: Path, lock_path: Path, base_dir: Path) -> None:
    """Resolve every declared skill to its current commit and write skills.lock."""
    manager = SkillManager(
        config_path=config_path,
        lock_path=lock_path,
        base_dir=base_dir,
    )
    config = manager._load_config()
    refreshed = {}
    for skill_key in config.skills:
        workflow, capability = manager._parse_skill_key(skill_key)
        cache_dir = manager._resolve_cache_dir(workflow, capability)
        with manager._cache_lock_for(cache_dir):
            refreshed[skill_key] = manager._refresh_source(
                skill_key,
                config.skills[skill_key],
                cache_dir,
            )
    with manager._lockfile_lock:
        manager._write_lock_unlocked(SkillsLock(skills=refreshed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh skills.lock")
    parser.add_argument("--config", default="config/skills.yaml", type=Path)
    parser.add_argument("--lock", default="config/skills.lock", type=Path)
    parser.add_argument(
        "--base-dir",
        default=str(Path.home() / ".agents" / "skills" / "agent-legion"),
        type=Path,
    )
    args = parser.parse_args()
    refresh_lock(args.config, args.lock, args.base_dir)


if __name__ == "__main__":
    main()
