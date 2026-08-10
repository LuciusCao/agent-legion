from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _git_env() -> dict[str, str]:
    """Strip parent-repo git env vars so ``git -C`` uses skill_dir's own repo."""
    env = {**dict(os.environ)}
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def _git(skill_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(skill_dir), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=_git_env(),
    )


def resolve_skill_version(skill_dir: Path) -> str:
    """Return ``tag@commit`` or just ``commit`` for a git-backed skill dir."""
    if not (skill_dir / ".git").is_dir():
        return ""
    try:
        commit = _git(skill_dir, "rev-parse", "HEAD").stdout.strip()
        if not commit:
            return ""
        tag = _git(skill_dir, "describe", "--tags", "--exact-match")
        if tag.returncode == 0:
            return f"{tag.stdout.strip()}@{commit}"
        return commit
    except Exception:
        logger.debug("failed to resolve skill version in %s", skill_dir, exc_info=True)
        return ""
