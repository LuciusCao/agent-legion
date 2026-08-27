"""Read-only git helpers for skill repositories (issue #217).

A skill repository is the git repo backing one skill key: the in-place
local source under the managed skills base dir, or the cache clone of a
URL source. These helpers list tags, resolve a tag to its commit, and
read the skill's text files at a commit without touching the working
tree (``git show``), so previewing a tag never disturbs the locked
checkout. All functions are read-only; the editing flow lives in
``services/skill_editing.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from server.app.services.job_errors import NotFoundError

TEXT_EXTENSIONS = {".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
MAX_FILE_BYTES = 128 * 1024
GIT_TIMEOUT_SECONDS = 10


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
        if check:
            raise NotFoundError(f"git unavailable for skill repo {repo_dir}: {exc}") from exc
        return subprocess.CompletedProcess(args=["git"], returncode=128, stdout=b"", stderr=b"")
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise NotFoundError(f"git command failed for skill repo {repo_dir}: {stderr.strip()}")
    return result


def is_git_repo(repo_dir: Path) -> bool:
    return repo_dir.is_dir() and (repo_dir / ".git").is_dir()


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


def head_commit(repo_dir: Path) -> str | None:
    result = run_git(repo_dir, ["rev-parse", "HEAD"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def read_files_at_commit(repo_dir: Path, commit: str) -> list[dict[str, Any]]:
    """Skill text files (SKILL.md + references/ + scripts/) at ``commit``.

    Same selection and shaping as the working-tree catalog read: text
    extensions only, symlinks skipped, content capped at MAX_FILE_BYTES.
    """
    listing = run_git(repo_dir, ["ls-tree", "-r", "-z", commit])
    entries = listing.stdout.decode("utf-8", errors="replace").split("\0")
    files: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        meta_parts = meta.split(" ")
        if len(meta_parts) < 2 or meta_parts[1] != "blob" or meta_parts[0] == "120000":
            continue
        if path != "SKILL.md" and not path.startswith(("references/", "scripts/")):
            continue
        if Path(path).suffix.lower() not in TEXT_EXTENSIONS:
            continue
        raw = run_git(repo_dir, ["show", f"{commit}:{path}"]).stdout
        files.append(
            {
                "path": path,
                "size": len(raw),
                "content": raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"),
                "truncated": len(raw) > MAX_FILE_BYTES,
            }
        )
    files.sort(key=lambda item: (item["path"] != "SKILL.md", item["path"]))
    return files


def detail_at_ref(skill_key: str, ref: str, repo_dir: Path) -> dict[str, Any]:
    """Skill detail pinned to tag ``ref``; the lock and checkout stay untouched."""
    if not is_git_repo(repo_dir):
        raise NotFoundError(f"Skill {skill_key!r} has no local git repository")
    commit = resolve_tag(repo_dir, ref)
    if commit is None:
        raise NotFoundError(f"Skill {skill_key!r} has no tag {ref!r}")
    return {
        "key": skill_key,
        "ref": ref,
        "commit": commit,
        "available": True,
        "tags": list(list_tags(repo_dir)),
        "files": read_files_at_commit(repo_dir, commit),
    }
