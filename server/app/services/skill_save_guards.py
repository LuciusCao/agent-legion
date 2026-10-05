"""Repo-state guards for the skill save pipeline (split from skill_editing).

Tag-name/collision, dirty-tree and untracked-overwrite checks shared by
``SkillEditingService._save_version_locked``; extracted for the file-size
budget with no behavior change (the git runner stays injectable so tests
keep monkeypatching the service's ``_git``).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from server.app.services import skill_repo
from server.app.services.job_errors import ConflictError
from server.app.services.skill_build_residue import is_build_residue
from server.app.services.skill_repo_edit import SkillEditValidationError

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def check_tag(run_git: GitRunner, skill_key: str, repo_dir: Path, new_tag: str) -> None:
    """Refuse malformed tag names and collisions (pre-write, all-or-nothing)."""
    # `git check-ref-format refs/tags/-l` passes (the dash rule covers the
    # refname, not path components) while `git tag -l` would silently list
    # instead of creating — refuse dash-leading tags outright.
    error = None
    if new_tag.startswith("-"):
        error = "tag names must not start with '-'"
    elif run_git(repo_dir, ["check-ref-format", f"refs/tags/{new_tag}"], check=False).returncode:
        error = f"tag {new_tag!r} is not a valid git ref name"
    if error:
        raise SkillEditValidationError(
            f"Invalid tag name: {new_tag!r}", [{"path": ".", "error": error}]
        )
    if new_tag in skill_repo.list_tags(repo_dir):
        raise ConflictError(f"Skill {skill_key!r} repo already has tag {new_tag!r}")


def check_clean(run_git: GitRunner, skill_key: str, repo_dir: Path) -> None:
    """Refuse a dirty tree — except UNSTAGED build residue (#1038).

    A local validator run rewrites ``__pycache__/*.pyc`` (tracked in older
    repos without a .gitignore) or drops new untracked ones; neither is the
    author's change, so they must not wedge the save. Exempt only entries
    whose index column is clean (``' '`` modified-in-worktree / ``'?'``
    untracked): the save commits the index (``git add -- <written>`` then a
    path-less ``git commit``), so an unstaged residue change can never ride
    the commit, while a STAGED one still refuses. ``-uall`` lists untracked
    files individually (a collapsed ``?? scripts/`` would hide whether the
    directory holds anything but residue); ``-z`` keeps paths unquoted.
    The index is never rewritten here (no ``git rm --cached``).
    """
    status = run_git(
        repo_dir, ["status", "--porcelain", "-z", "--untracked-files=all"], check=False
    )
    if status.returncode != 0 or _blocking_entries(status.stdout):
        raise ConflictError(
            f"Skill {skill_key!r} repo has uncommitted changes; commit or revert them first"
            " (unstaged build residue such as __pycache__/ or *.pyc is ignored)"
        )


def _blocking_entries(porcelain_z: str) -> list[str]:
    """Dirty entries of ``git status --porcelain -z`` that block a save."""
    blocking: list[str] = []
    tokens = porcelain_z.split("\0")
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            index += 1  # the rename/copy source path follows as its own token
            blocking.append(path)
            continue
        if code[0] in " ?" and is_build_residue(path):
            continue
        blocking.append(path)
    return blocking


def check_overwrites(
    run_git: GitRunner, skill_key: str, repo_dir: Path, targets: list[tuple[Path, str]]
) -> None:
    """Refuse to overwrite a pre-existing UNTRACKED file: rolling back a
    write to such a file could not restore its original content."""
    errors: list[dict[str, str]] = []
    for path, _ in targets:
        relative = path.relative_to(repo_dir.resolve()).as_posix()
        tracked = run_git(repo_dir, ["ls-files", "--error-unmatch", "--", relative], check=False)
        if tracked.returncode != 0 and path.exists():
            errors.append({"path": relative, "error": "refusing to overwrite an untracked file"})
    if errors:
        raise SkillEditValidationError("Unsafe skill file overwrite", errors)
