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
from server.app.services.skill_commit_snapshot import commit_snapshot
from server.app.services.skill_edit_snapshot import text_file


def read_files_at_commit(
    repo_dir: Path, commit: str, *, for_edit: bool = False
) -> list[dict[str, Any]]:
    """Skill text files (SKILL.md + root contract.yaml + references/ +
    scripts/) at ``commit``.

    Display reads filter text extensions and cap content. Editing reads
    instead use the complete, bounded Git snapshot contract.
    """
    if for_edit:
        return commit_snapshot(repo_dir, commit)
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
        if path not in ("SKILL.md", "contract.yaml") and not path.startswith(
            ("references/", "scripts/")
        ):
            continue
        if Path(path).suffix.lower() not in skill_repo.TEXT_EXTENSIONS:
            continue
        raw = skill_repo.run_git(repo_dir, ["show", f"{commit}:{path}"]).stdout
        files.append(text_file(path, raw))
    files.sort(key=lambda item: (item["path"] not in ("SKILL.md", "contract.yaml"), item["path"]))
    return files


def detail_at_ref(
    skill_key: str, ref: str, repo_dir: Path, *, for_edit: bool = False
) -> dict[str, Any]:
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
        "files": read_files_at_commit(repo_dir, commit, for_edit=for_edit),
    }


def skill_detail(
    skill_key: str,
    repo_dir: Path,
    ref: str | None,
    working_tree_reader: Callable[[Path], list[dict[str, Any]]],
    *,
    for_edit: bool = False,
) -> dict[str, Any]:
    """Default (no-ref) skill detail plus the ``ref`` preview dispatch.

    Display detail previews the working tree. Editing detail reads the
    resolved HEAD commit, matching dispatch's committed content rather than
    exposing host-local changes. The reported commit identifies that snapshot.
    """
    if for_edit and not skill_repo.is_git_repo(repo_dir):
        raise NotFoundError(f"Skill {skill_key!r} has no local git repository")
    if ref is not None:
        return detail_at_ref(skill_key, ref, repo_dir, for_edit=for_edit)
    tags = list(skill_repo.list_tags(repo_dir)) if skill_repo.is_git_repo(repo_dir) else []
    commit = skill_repo.head_commit(repo_dir) or ""
    if for_edit and not commit:
        raise NotFoundError(f"Skill {skill_key!r} has no committed editing snapshot")
    available = repo_dir.is_dir()
    return {
        "key": skill_key,
        "ref": "latest",
        "commit": commit,
        "available": available,
        "tags": tags,
        "files": read_files_at_commit(repo_dir, commit, for_edit=True)
        if for_edit
        else working_tree_reader(repo_dir)
        if available
        else [],
    }
