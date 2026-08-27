"""Skill detail builders over the skill-repo git primitives (issue #217).

``read_files_at_commit`` reads the skill's text files at a commit via
``git show`` without touching the working tree; ``detail_at_ref`` /
``skill_detail`` shape the preview-endpoint responses: a tag preview,
or the default detail serving the LOCKED commit's content when the
skill lock pins one (falling back to the working tree in the seed
scenario). Split from ``services/skill_repo.py`` for the file budget.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.services import skill_repo
from server.app.services.job_errors import NotFoundError
from server.app.skills.config import LockedSkillSource


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
    configured_ref: str,
    repo_dir: Path,
    locked: LockedSkillSource | None,
    ref: str | None,
    working_tree_reader: Callable[[Path], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Default (no-ref) skill detail plus the ``ref`` preview dispatch.

    With a lock entry whose commit is present in the repo, the default
    detail reads the LOCKED commit's content — the "current locked
    version" the Studio panel labels it as — instead of the working
    tree (steady state is identical: relock checks the lock out). With
    no usable lock (seed scenario) it falls back to the working tree.
    """
    if ref is not None:
        return detail_at_ref(skill_key, ref, repo_dir)
    tags = list(skill_repo.list_tags(repo_dir)) if skill_repo.is_git_repo(repo_dir) else []
    locked_commit = locked.commit if locked is not None else ""
    if locked_commit and skill_repo.has_commit(repo_dir, locked_commit):
        return {
            "key": skill_key,
            "ref": configured_ref,
            "commit": locked_commit,
            "available": True,
            "tags": tags,
            "files": read_files_at_commit(repo_dir, locked_commit),
        }
    available = repo_dir.is_dir()
    return {
        "key": skill_key,
        "ref": configured_ref,
        "commit": locked_commit,
        "available": available,
        "tags": tags,
        "files": working_tree_reader(repo_dir) if available else [],
    }
