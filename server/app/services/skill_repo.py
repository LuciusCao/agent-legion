"""Read-only git primitives for skill repositories (issue #217).

A skill repository is the git repo backing one skill key: the in-place
local source under the managed skills base dir, a non-in-place local
source path, or the cache clone of a URL source. All functions here are
read-only; detail builders live in ``services/skill_detail.py`` and the
editing flow in ``services/skill_editing.py``.

Error taxonomy: absence semantics (unknown skill/tag) raise
``NotFoundError`` (404); operational git failures raise
``SkillGitError``, which no route maps — it propagates as a 500. Client
error messages never carry host absolute paths (they would leak to
scoped tokens and workspace members); paths go to the server log.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from server.app.services.job_errors import JobServiceError

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
MAX_FILE_BYTES = 128 * 1024
GIT_TIMEOUT_SECONDS = 10


class SkillGitError(JobServiceError):
    """Operational git failure on a skill repo (unmapped by routes -> 500)."""


def run_git(
    repo_dir: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=False,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("git unavailable for skill repo %s: %s", repo_dir, exc)
        if check:
            raise SkillGitError("git is unavailable for the skill repository") from exc
        return subprocess.CompletedProcess(args=["git"], returncode=128, stdout=b"", stderr=b"")
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        logger.error("git %s failed for skill repo %s: %s", args[0], repo_dir, stderr.strip())
        raise SkillGitError(f"git {args[0]} failed for the skill repository")
    return result


def is_git_repo(repo_dir: Path) -> bool:
    return repo_dir.is_dir() and (repo_dir / ".git").is_dir()


def local_repo_path(repo: str) -> Path | None:
    """The resolved local path of a skill source repo, or None for URL sources."""
    if repo.startswith("~/") or Path(repo).is_absolute():
        return Path(repo).expanduser().resolve()
    return None


def list_tags(repo_dir: Path) -> tuple[str, ...]:
    """Git tags of the repo, latest version first (``-version:refname``)."""
    result = run_git(repo_dir, ["tag", "--list", "--sort=-version:refname"], check=False)
    if result.returncode != 0:
        return ()
    return tuple(
        line.strip()
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    )


def resolve_tag(repo_dir: Path, tag: str) -> str | None:
    """Commit a tag points at (annotated tags peeled), or None if absent."""
    result = run_git(
        repo_dir, ["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def has_commit(repo_dir: Path, commit: str) -> bool:
    result = run_git(repo_dir, ["cat-file", "-t", commit], check=False)
    return result.returncode == 0 and b"commit" in result.stdout


def head_commit(repo_dir: Path) -> str | None:
    result = run_git(repo_dir, ["rev-parse", "HEAD"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()
