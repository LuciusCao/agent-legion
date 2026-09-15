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
``v0.1.0``. Non-semver tags are ignored for the computation. The tag is
selected INSIDE the skill repo lock (codex P2): two propagations racing
the same skill no longer compute the same patch tag outside the lock —
the waiter re-reads the tags the winner left behind.

Concurrency (codex P1): the batch plan is taken under the ``_shared``
edit lock (map + per-source content digests + source bytes), then
RELEASED — the shared lock must not be held across the batch. Each
skill's save then re-acquires it INSIDE the skill repo lock (order
skill → shared) and HOLDS it for the whole per-skill critical section:
generation recheck, sync-plan pinning, skip judgment and the file
application all complete before it is released. A concurrent full-state
PUT therefore either lands first (the recheck aborts the batch with a
retryable ``ConflictError``, 409) or after the skill's commit — never in
between, so a stale plan is never applied to a new generation (missed
new targets / removed targets / replaced sources).

Per-skill isolation: one skill's failure (dirty tree, unreadable shared
source, contract regression, git error) never aborts the batch — every
skill reports ``synced`` (new tag) / ``skipped`` (already in sync, or no
git repo) / ``failed`` (reason). The generation conflict above is the one
deliberate exception: continuing would apply a stale plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_editing import SkillEditingService
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_propagate_apply import propagate_one
from server.app.services.skill_shared_propagate_plan import (
    PropagateResult,
    PropagateSkillResult,
)
from server.app.services.skill_shared_propagate_plan import (
    read_generation as _read_generation,
)
from server.app.services.skill_shared_propagate_plan import (
    read_source_bytes as _read_source_bytes,
)
from server.app.services.skill_shared_store import SHARED_DIR_NAME, shared_edit_lock
from server.app.services.skill_shared_sync import load_shared_map
from server.app.skills.skill_roots import skills_root, workspace_skill_dir

__all__ = [
    "PropagateResult",
    "PropagateSkillResult",
    "propagate_shared_materials",
]


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
        all_sources = [m.source for m in shared_map.materials]
        generation = _read_generation(shared_dir, all_sources)
        # Source bytes pinned to the planned generation; the per-skill
        # recheck guarantees they are still current when the save applies.
        shared_bytes = _read_source_bytes(shared_dir, all_sources)
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
    selected: set[str] = set()
    all_mapped: dict[str, list[str]] = {}
    for material in shared_map.materials:
        for skill in material.skills:
            all_mapped.setdefault(skill, []).append(material.source)
            if requested is None or material.source in requested:
                selected.add(skill)
    editing = SkillEditingService(base_dir=base, runs_dir=runs_dir)
    return PropagateResult(
        results=tuple(
            propagate_one(
                workspace_id,
                skill,
                tuple(all_mapped[skill]),
                shared_dir,
                generation,
                shared_bytes,
                editing,
            )
            for skill in sorted(selected)
        )
    )
