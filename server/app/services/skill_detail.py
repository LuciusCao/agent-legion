"""Skill detail builders over the skill-repo git primitives (issue #217).

``read_files_at_commit`` reads the skill's text files at a commit via
``git show`` without touching the working tree; ``detail_at_ref`` /
``skill_detail`` shape the preview-endpoint responses: a tag preview,
or the default detail serving the working tree at HEAD (``latest`` —
the #322 unpinned-ref semantics; the lock pins only explicit tag refs).
Split from ``services/skill_repo.py`` for the file budget.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.services import skill_repo
from server.app.services.job_errors import NotFoundError


def read_files_at_commit(repo_dir: Path, commit: str) -> list[dict[str, Any]]:
    """Skill text files (SKILL.md + references/ + scripts/) at ``commit``.

    Same selection and shaping as the working-tree catalog read: text
    extensions only, symlinks skipped, content capped at MAX_FILE_BYTES.
    """
    listing = skill_repo.run_git(repo_dir, ["ls-tree", "-r", "-z", commit])
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
        if Path(path).suffix.lower() not in skill_repo.TEXT_EXTENSIONS:
            continue
        raw = skill_repo.run_git(repo_dir, ["show", f"{commit}:{path}"]).stdout
        files.append(
            {
                "path": path,
                "size": len(raw),
                "content": raw[: skill_repo.MAX_FILE_BYTES].decode("utf-8", errors="replace"),
                "truncated": len(raw) > skill_repo.MAX_FILE_BYTES,
            }
        )
    files.sort(key=lambda item: (item["path"] != "SKILL.md", item["path"]))
    return files


def detail_at_ref(skill_key: str, ref: str, repo_dir: Path) -> dict[str, Any]:
    """Skill detail pinned to tag ``ref``; the lock and checkout stay untouched."""
    if not skill_repo.is_git_repo(repo_dir):
        raise NotFoundError(f"Skill {skill_key!r} has no local git repository")
    commit = skill_repo.resolve_tag(repo_dir, ref)
    if commit is None:
        raise NotFoundError(f"Skill {skill_key!r} has no tag {ref!r}")
    return {
        "key": skill_key,
        "ref": ref,
        "commit": commit,
        "available": True,
        "tags": list(skill_repo.list_tags(repo_dir)),
        "files": read_files_at_commit(repo_dir, commit),
    }


def skill_detail(
    skill_key: str,
    repo_dir: Path,
    ref: str | None,
    working_tree_reader: Callable[[Path], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Default (no-ref) skill detail plus the ``ref`` preview dispatch.

    The default detail reads the working tree at HEAD — the ``latest``
    semantics (#322): an unpinned node ref follows the repo's current HEAD,
    so the working tree IS the content a default dispatch would run. The
    reported commit is HEAD's (empty when the repo is missing/has none).
    """
    if ref is not None:
        return detail_at_ref(skill_key, ref, repo_dir)
    tags = list(skill_repo.list_tags(repo_dir)) if skill_repo.is_git_repo(repo_dir) else []
    commit = skill_repo.head_commit(repo_dir) or ""
    available = repo_dir.is_dir()
    return {
        "key": skill_key,
        "ref": "latest",
        "commit": commit,
        "available": available,
        "tags": tags,
        "files": working_tree_reader(repo_dir) if available else [],
    }
