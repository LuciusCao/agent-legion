"""Propagate shared materials into mapped skill repos (issue #673).

The write counterpart of the drift view (#643): for every map entry
(optionally filtered to the requested sources), copy the ``_shared``
source into each mapped skill's in-place repo at the same relative path,
commit and tag a new version. The write itself REUSES
``SkillEditingService.save_version`` with an empty hand-supplied file
list — the save-time sync (``skill_shared_sync.plan_shared_sync``)
injects exactly the skill's mapped materials, so path safety, untracked-
overwrite refusal, contract re-check, the ``agent-legion-studio`` commit
identity, ``--no-verify`` and the all-or-nothing rollback stay one code
path with ``save_skill_version``. The DB skill lock is never touched and
node pins do not move (``latest`` nodes pick the new HEAD up on their
next dispatch; pinned nodes keep their locked commit until relocked).

Propagation granularity is the SKILL's whole mapped set, not the single
requested source: ``save_version`` always syncs every material mapped to
the skill. The sources filter only selects WHICH skills run (those
mapped to at least one requested source); the per-skill ``synced_files``
reports what actually landed.

Tag rule: the highest ``vMAJOR.MINOR.PATCH`` tag gets patch +1
(``v1.2.3`` → ``v1.2.4``); a repo without any version tag starts at
``v0.1.0``. Non-semver tags are ignored for the computation; a collision
is rejected by ``save_version``'s own tag check and surfaces as that
skill's failure.

Per-skill isolation: one skill's failure (dirty tree, unreadable shared
source, contract regression, git error) never aborts the batch — every
skill reports ``synced`` (new tag) / ``skipped`` (already in sync, or no
git repo) / ``failed`` (reason). The shared map is read once under the
``_shared`` edit lock and RELEASED before any save: ``save_version``
acquires skill lock → shared lock in that order, so holding the shared
lock across the batch would invert the lock order.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from server.app.services import skill_repo
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.skill_editing import SkillEditingService
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_store import SHARED_DIR_NAME, shared_edit_lock
from server.app.services.skill_shared_sync import load_shared_map
from server.app.skills.skill_roots import skills_root, workspace_skill_dir

logger = logging.getLogger(__name__)

_VERSION_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
INITIAL_VERSION_TAG = "v0.1.0"

PropagateStatus = Literal["synced", "skipped", "failed"]


@dataclass(frozen=True)
class PropagateSkillResult:
    skill: str
    status: PropagateStatus
    tag: str | None = None
    detail: str | None = None
    synced_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class PropagateResult:
    results: tuple[PropagateSkillResult, ...]


def next_version_tag(tags: Sequence[str]) -> str:
    """Highest ``vX.Y.Z`` tag with patch +1; ``v0.1.0`` when none parse."""
    best: tuple[int, int, int] | None = None
    for tag in tags:
        match = _VERSION_TAG_RE.fullmatch(tag)
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or version > best:
            best = version
    if best is None:
        return INITIAL_VERSION_TAG
    return f"v{best[0]}.{best[1]}.{best[2] + 1}"


def _head_matches(repo_dir: Path, source: str, shared_bytes: bytes) -> bool:
    result = skill_repo.run_git(repo_dir, ["show", f"HEAD:{source}"], check=False)
    return result.returncode == 0 and result.stdout == shared_bytes


def _propagate_one(
    workspace_id: str,
    skill: str,
    mapped_sources: tuple[str, ...],
    shared_dir: Path,
    editing: SkillEditingService,
) -> PropagateSkillResult:
    skill_key = f"{workspace_id}/{skill}"
    repo_dir = workspace_skill_dir(workspace_id, base_dir=editing.base_dir) / skill
    if not skill_repo.is_git_repo(repo_dir):
        return PropagateSkillResult(skill=skill, status="skipped", detail="skill repo not found")
    shared_bytes: dict[str, bytes] = {}
    for source in mapped_sources:
        try:
            shared_bytes[source] = (shared_dir / source).read_bytes()
        except OSError:
            return PropagateSkillResult(
                skill=skill, status="failed", detail=f"shared source unreadable: {source}"
            )
    if all(_head_matches(repo_dir, source, data) for source, data in shared_bytes.items()):
        return PropagateSkillResult(skill=skill, status="skipped", detail="already in sync")
    tag = next_version_tag(skill_repo.list_tags(repo_dir))
    message = f"Sync shared materials: {', '.join(mapped_sources)}"
    try:
        outcome = editing.save_version(skill_key, [], tag, message)
    except JobServiceError as exc:
        # Mapped save failures (dirty tree 409, contract regression 422,
        # tag conflict, git operational error) — isolated to this skill.
        return PropagateSkillResult(skill=skill, status="failed", detail=str(exc))
    except Exception as exc:
        # #204 broad-except audit: per-skill isolation must hold for ANY
        # save failure mode, including ones outside the JobServiceError
        # taxonomy (e.g. SkillRollbackError after a failed rollback or a
        # programming error). Swallowing into a per-skill `failed` result
        # is the batch contract — one repo's problem must not strand the
        # others; the full traceback goes to the server log, the client
        # gets the exception type only (messages may carry host paths).
        logger.exception("shared-material propagate failed for skill %s", skill_key)
        return PropagateSkillResult(
            skill=skill, status="failed", detail=f"unexpected error ({type(exc).__name__})"
        )
    return PropagateSkillResult(
        skill=skill,
        status="synced",
        tag=str(outcome["tag"]),
        synced_files=tuple(outcome["synced_files"]),
    )


def propagate_shared_materials(
    workspace_id: str,
    sources: Sequence[str] | None = None,
    *,
    base_dir: Path | None = None,
    runs_dir: Path | None = None,
) -> PropagateResult:
    """Propagate map entries (all, or only the requested sources) per skill.

    A workspace without ``_shared`` is a 404; requested sources that the
    current map does not declare are a pre-write 422 (nothing is saved).
    """
    base = base_dir or skills_root()
    shared_dir = workspace_skill_dir(workspace_id, base_dir=base) / SHARED_DIR_NAME
    with shared_edit_lock(shared_dir, base):
        shared_map = load_shared_map(shared_dir)
    if shared_map is None:
        raise NotFoundError("Workspace has no shared materials (_shared)")
    requested = None if sources is None else set(sources)
    if requested is not None:
        unknown = sorted(requested - {m.source for m in shared_map.materials})
        if unknown:
            raise SkillEditValidationError(
                "Unknown shared material sources",
                [
                    {"path": source, "error": "source is not declared in the shared map"}
                    for source in unknown
                ],
            )
    # The sources filter selects WHICH skills run (mapped to at least one
    # requested source); the skip check and commit message see the skill's
    # WHOLE mapped set, because save_version syncs all of it.
    selected: dict[str, list[str]] = {}
    all_mapped: dict[str, list[str]] = {}
    for material in shared_map.materials:
        for skill in material.skills:
            all_mapped.setdefault(skill, []).append(material.source)
            if requested is None or material.source in requested:
                selected.setdefault(skill, [])
    editing = SkillEditingService(base_dir=base, runs_dir=runs_dir)
    return PropagateResult(
        results=tuple(
            _propagate_one(workspace_id, skill, tuple(all_mapped[skill]), shared_dir, editing)
            for skill in sorted(selected)
        )
    )
