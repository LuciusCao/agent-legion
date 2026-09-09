"""Shared (skill_key, commit) materialization cache for output validation (#569).

The Worker-result validation path used to pay one full ``git archive``
materialization per result (``checkout_skill_commit`` under the per-repo
FileLock) — a completion wave over one skill serialized every result on the
same lock and re-exported byte-identical trees. A commit id is immutable, so
its exported tree is cacheable forever; this module materializes each
(skill, commit) pair at most once per host into a shared subtree of the
runs dir:

    <runs_dir>/.shared/<workflow>/<capability>/<commit40>/<workflow>/<capability>/

The bucket is the two-component nested path, NOT a flattened
``<workflow>--<capability>`` name: skill-key components may themselves
contain ``--``, so flattening would collide distinct skills (``a--b/c`` vs
``a/b--c``) into one bucket whose per-skill LRU eviction runs under the
WRONG skill's FileLock and could rmtree a tree another skill's validator
is reading (PR #571 review).

A cache hit is a pure path probe: the ``.complete`` marker file inside the
commit dir is written AFTER the atomic rename of a fully exported tree, so
its presence certifies integrity — a crashed export leaves a markerless
directory that is never treated as a hit (and is reclaimed on the next
materialization under the same lock). No git subprocess runs on a hit.

The layout keeps the ``run_dir.parents[1]`` contract of
``workflows.skills.resolve_workflow_skill`` (the materialized skill dir is
``<root>/<workflow>/<capability>`` under root ``<commit40>``), so the
contract check callers already run applies unchanged.

The shared tree is a READ-ONLY source of truth — validators never run
against it directly (PR #571 codex P1s): a validator script may write next
to its own ``__file__`` (which would pollute the cached tree the
``.complete`` marker then vouches for), and an LRU eviction must never
rmtree a tree mid-validation. ``materialized_private_copy`` therefore
copies the cached tree (``shutil.copytree`` — dereferencing symlinks, NOT
hardlinks: hardlinked files share inodes, so a validator writing an
existing file would rewrite the cache) into a per-validation private dir of
the legacy ``runs_dir/validate-<uuid>/`` shape, while still holding the
per-repo FileLock. Eviction runs under the same lock, so it can never
interleave with the copy window; the validator then reads only the private
copy and is immune to eviction outright — no refcounting or read leases
needed. The private copy is reclaimed by ``SkillManager.cleanup_execution``
after validation (and by the age-based sweeper as the crash backstop).

Cache governance: bounded per skill — the cache keeps the most recent
``KEPT_COMMITS_PER_SKILL`` commit materializations, evicting oldest-first
by commit-dir mtime (a hit touches the mtime, so the policy is LRU). The
runs-dir sweeper never enters ``.shared`` (runs_gc skips it).
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from server.app.skills.config import LATEST_REF, SkillsLock
from server.app.skills.errors import SkillRepoError
from server.app.skills.manager import _COMMIT_RE, SkillManager
from server.app.skills.materialize import export_commit
from server.app.skills.runs_gc import SHARED_DIR_NAME

KEPT_COMMITS_PER_SKILL = 4
COMPLETE_MARKER = ".complete"
_TMP_PREFIX = ".tmp-"


class NullSkillStore:
    """Store for managers that only serve exact-commit materialization.

    The cached-commit path never reads or writes the skill lock (a commit id
    needs no resolution), so pool workers construct their SkillManager with
    this no-op store instead of a DB handle — the spawn-context constraint
    forbids carrying one across the process boundary anyway.
    """

    def get_lock(self) -> SkillsLock | None:
        return None

    def put_lock(self, lock: SkillsLock) -> None:
        raise SkillRepoError("NullSkillStore never writes the skill lock")


def shared_cache_root(runs_dir: Path) -> Path:
    return Path(runs_dir) / SHARED_DIR_NAME


def resolve_skill_commit(manager: SkillManager, skill_key: str, ref: str | None) -> str:
    """Resolve ``ref`` to a commit WITHOUT materializing (legacy manifests).

    Same resolution semantics as ``SkillManager.checkout_skill`` (latest =
    live HEAD, anything else via the DB-backed lock) but stops at the commit
    id: materialization is the cache's job. May read/write the lock document
    (pinning a first-seen tag), so callers run it in the main process, never
    inside a pool worker.
    """
    workflow, capability = manager._parse_skill_key(skill_key)
    cache_dir = manager._resolve_cache_dir(workflow, capability)
    effective_ref = ref or LATEST_REF
    with manager._cache_lock_for(cache_dir):
        if effective_ref == LATEST_REF:
            return manager._resolve_latest(skill_key, cache_dir)
        return manager._resolve_pinned(skill_key, cache_dir, effective_ref)


def materialized_commit_dir(manager: SkillManager, skill_key: str, commit: str) -> Path:
    """Return the shared cached tree of (``skill_key``, ``commit``).

    Read-only shared source: callers that hand the tree to a validator must
    go through ``materialized_private_copy`` instead (see module docstring).
    """
    workflow, capability = manager._parse_skill_key(skill_key)
    cache_dir = manager._resolve_cache_dir(workflow, capability)
    with manager._cache_lock_for(cache_dir):
        return _cached_commit_tree(manager, skill_key, commit, cache_dir, workflow, capability)


def materialized_private_copy(
    manager: SkillManager, skill_key: str, commit: str, validation_id: str
) -> Path:
    """Materialize via the shared cache, then copy out a private validation tree.

    Cache probe/materialization AND the ``copytree`` both run under the
    per-repo FileLock, so per-skill eviction (same lock) can never delete
    the source tree mid-copy; the returned private dir
    (``runs_dir/<validation_id>/<workflow>/<capability>``, the legacy
    per-execution shape the sweeper and ``cleanup_execution`` already know)
    is the validator's to read AND write — nothing it does can leak back
    into the shared cache.
    """
    workflow, capability = manager._parse_skill_key(skill_key)
    cache_dir = manager._resolve_cache_dir(workflow, capability)
    run_dir = manager._resolve_run_dir(validation_id, workflow, capability)
    with manager._cache_lock_for(cache_dir):
        cached = _cached_commit_tree(manager, skill_key, commit, cache_dir, workflow, capability)
        shutil.copytree(cached, run_dir)
    return run_dir


def _cached_commit_tree(
    manager: SkillManager,
    skill_key: str,
    commit: str,
    cache_dir: Path,
    workflow: str,
    capability: str,
) -> Path:
    """The lock-held guts of the cache: probe, materialize on miss, evict.

    Caller must hold ``manager._cache_lock_for(cache_dir)``. Hit (marker
    file present): pure path probe, zero git calls. Miss: export the
    commit's tree to a temp sibling, atomically rename into place, then
    drop the integrity marker.
    """
    if not _COMMIT_RE.fullmatch(commit):
        raise SkillRepoError(f"skill commit must be a 40-hex sha: {commit!r}")
    skill_root = shared_cache_root(manager.runs_dir) / workflow / capability
    commit_dir = skill_root / commit
    marker = commit_dir / COMPLETE_MARKER
    run_dir = commit_dir / workflow / capability
    if marker.is_file() and run_dir.is_dir():
        # LRU touch: eviction ranks by commit-dir mtime.
        os.utime(commit_dir)
        return run_dir
    if commit_dir.exists():
        # Markerless leftover from a crashed export — never a hit.
        shutil.rmtree(commit_dir)
    manager._require_cache_dir(skill_key, cache_dir)
    if not manager._has_commit(cache_dir, commit):
        raise SkillRepoError(f"commit {commit!r} is missing from {cache_dir}")
    tmp_dir = skill_root / f"{_TMP_PREFIX}{commit}-{uuid.uuid4().hex}"
    export_commit(
        manager._run_git, manager.runs_dir, cache_dir, commit, tmp_dir / workflow / capability
    )
    os.rename(tmp_dir, commit_dir)
    # Marker AFTER the atomic rename: presence == complete tree.
    marker.write_text("ok\n")
    _evict_old_commits(skill_root)
    return run_dir


def _evict_old_commits(skill_root: Path) -> None:
    """Bound the per-skill cache: drop crash leftovers, LRU-trim to the cap."""
    # Called under the per-repo FileLock, so any .tmp-* export dir present
    # here is a crash leftover (no concurrent export of this skill exists),
    # and any markerless commit dir is a half-rename — both are reclaimed.
    # In-flight validations are safe: they run against private copies, and
    # the copying window itself holds this same lock.
    complete = []
    for entry in skill_root.iterdir():
        if not entry.is_dir():
            continue
        if _COMMIT_RE.fullmatch(entry.name) and (entry / COMPLETE_MARKER).is_file():
            complete.append(entry)
        else:
            shutil.rmtree(entry, ignore_errors=True)
    complete.sort(key=lambda entry: entry.stat().st_mtime)
    while len(complete) > KEPT_COMMITS_PER_SKILL:
        shutil.rmtree(complete.pop(0), ignore_errors=True)
