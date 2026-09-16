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
    status = run_git(repo_dir, ["status", "--porcelain"], check=False)
    if status.returncode != 0 or status.stdout.strip():
        raise ConflictError(
            f"Skill {skill_key!r} repo has uncommitted changes; commit or revert them first"
        )


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
