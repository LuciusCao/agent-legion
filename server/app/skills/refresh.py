"""Admin relock support: force-refresh a skill cache to its source ref.

Only the relock flow (``server.app.skills.lock.refresh_lock``) uses this;
runtime dispatch goes through ``SkillManager._ensure_cached`` instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.skills.config import LockedSkillSource, SkillSourceConfig
from server.app.skills.errors import SkillRepoError
from server.app.skills.manager import SkillManager

logger = logging.getLogger(__name__)


def refresh_source(
    manager: SkillManager,
    skill_key: str,
    source: SkillSourceConfig,
    cache_dir: Path,
) -> LockedSkillSource:
    """Fetch and check out the source ref in ``cache_dir``; return the pin."""
    repo = manager._normalize_repo(source.repo)
    in_place = manager._is_in_place_source(repo, cache_dir)
    if not cache_dir.exists():
        if in_place:
            raise SkillRepoError(
                f"local skill repo not found: {cache_dir} "
                "（示例 workflow 的 skill 请先运行 make import-demo 导入；"
                "其他 skill 请确认本地仓库路径与 skill 源配置一致）"
            )
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        manager._run_git(["clone", repo, str(cache_dir)])
        # A fresh clone may lack commits the old repo held (e.g. locked
        # commits detached from any ref), so drop the memoized presence
        # checks for this cache dir.
        manager._known_commits = {k for k in manager._known_commits if k[0] != str(cache_dir)}
    elif not (cache_dir / ".git").is_dir():
        raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

    commit = manager._resolve_source_ref(cache_dir, source.ref, in_place=in_place)
    manager._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
    manager._run_git(["-C", str(cache_dir), "clean", "-fd"])
    logger.info("Refreshed Pi skill %s to %s", skill_key, commit)
    return LockedSkillSource(repo=source.repo, ref=source.ref, commit=commit)
