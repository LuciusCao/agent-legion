"""Mutation-side plumbing for skill repo editing (issue #217, PR #224 review).

``edit_lock_for`` derives the repo-level cross-process FileLock that
serializes a save against other saves — and, for in-place sources,
against ``SkillManager``'s dispatch ``checkout -f``/``clean -fd`` (same
runs-dir lock directory, same lock file name). ``rollback_checked``
restores a repo to a recorded HEAD after a failed save and turns a
rollback failure into an explicit ``SkillRollbackError`` instead of
silently continuing in a partially applied state. ``run_edit_git`` is
the mutation-side git runner (operational failures raise
``SkillGitError``; host paths stay in the server log). Split from
``services/skill_editing.py`` for the file budget.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from collections.abc import Callable
from os.path import isdir as _isdir
from os.path import islink as _islink
from pathlib import Path

from filelock import FileLock

from server.app.services.job_errors import JobServiceError
from server.app.services.skill_repo import SkillGitError
from server.app.skills.paths import default_skills_runs_dir, ensure_secure_runs_dir

logger = logging.getLogger(__name__)

GIT_EDIT_TIMEOUT_SECONDS = 30


class SkillRollbackError(JobServiceError):
    """Rollback itself failed (unmapped by routes -> 500): the repo may be
    in a partially applied state and needs manual intervention."""


def run_edit_git(
    repo_dir: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=GIT_EDIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("git unavailable for skill repo %s: %s", repo_dir, exc)
        raise SkillGitError("git is unavailable for the skill repository") from exc
    if check and result.returncode != 0:
        logger.error(
            "git %s failed for skill repo %s: %s", args[0], repo_dir, result.stderr.strip()
        )
        raise SkillGitError(f"git {args[0]} failed for the skill repository")
    return result


def edit_lock_for(repo_dir: Path, base_dir: Path, runs_dir: Path | None) -> FileLock:
    """Repo-level cross-process lock from the skills runs dir.

    For in-place sources (repo inside the managed base dir) the file name
    matches ``SkillManager._cache_lock_for`` exactly; non-in-place local
    sources get a hash-derived name (dispatch only clones/fetches from
    them, which a commit cannot corrupt).
    """
    runs_root = ensure_secure_runs_dir(runs_dir or default_skills_runs_dir())
    lock_dir = runs_root / ".locks"
    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError:
        if _islink(lock_dir) or not _isdir(lock_dir):
            raise OSError(f"refusing to use skills lock dir {lock_dir}: not a directory") from None
        os.chmod(lock_dir, 0o700)
    try:
        repo_dir.relative_to(base_dir.resolve())
        name = f"{repo_dir.parent.name}--{repo_dir.name}.lock"
    except ValueError:
        digest = hashlib.sha256(str(repo_dir).encode()).hexdigest()[:16]
        name = f"edit-{digest}.lock"
    return FileLock(str(lock_dir / name))


def rollback_checked(
    skill_key: str,
    repo_dir: Path,
    head: str,
    written: list[Path],
    run_git: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """``git reset --hard <head>`` + a path-scoped ``git clean -fd``.

    The path scope means only the files this save wrote are removed,
    never unrelated untracked files; exactness comes from the up-front
    rejection of overwriting pre-existing untracked files. Both steps'
    return codes are checked: a failed rollback raises SkillRollbackError
    rather than letting the caller claim all-or-nothing.
    """
    relative = [path.relative_to(repo_dir.resolve()).as_posix() for path in written]
    reset = run_git(repo_dir, ["reset", "--hard", head], check=False)
    clean = run_git(repo_dir, ["clean", "-fd", "--", *relative], check=False)
    if reset.returncode != 0 or clean.returncode != 0:
        logger.error(
            "rollback failed for skill %s repo %s: reset rc=%s clean rc=%s",
            skill_key,
            repo_dir,
            reset.returncode,
            clean.returncode,
        )
        raise SkillRollbackError(
            f"Skill {skill_key!r} rollback failed; the repo may be in a partially "
            "applied state and needs manual intervention"
        )
