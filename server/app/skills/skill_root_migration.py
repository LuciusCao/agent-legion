"""Startup migration for the retired ``agent-legion`` skill-root prefix.

The skill root moved up from ``~/.agents/skills/agent-legion`` to
``~/.agents/skills`` (single source ``server.app.skills.skill_roots``), and the
compose mount moved with it. Deployments upgraded across that change still
hold ``skill_sources`` rows whose ``repo`` points at the old nested path,
which dangles under the new mount/layout: the skill catalog preview and
``save_skill_version`` follow the persisted ``repo``, and lock resolution
cannot rebuild inside the read-only ``:ro`` mount. This migration rewrites
the retired prefix in the persisted documents:

- every ``skill_sources`` entry whose ``repo`` starts with the old prefix
  (tilde display form — the shape the retired seed/tracked files wrote — or
  the expanded absolute form an operator may have saved via the admin API)
  is rewritten to the new root;
- the matching ``skill_lock`` entries are dropped wholesale: the locked
  commit belongs to the old location's repo and would not match the new
  in-place repo ("locked commit is missing"). The next dispatch or relock
  re-resolves them from ``ref`` — existing SkillManager/refresh behaviour.

Runs once per process in ``create_app`` right after ``seed_skill_sources``.
Idempotent: with no legacy prefix left there is nothing to rewrite and the
migration is a no-op (no writes, no log).
"""

from __future__ import annotations

import logging
import os

from server.app.db.dialect import ConnectSource
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillSourceConfig

logger = logging.getLogger(__name__)

_LEGACY_ROOT_TILDE = "~/.agents/skills/agent-legion"
_NEW_ROOT_TILDE = "~/.agents/skills"


def _rewrite_repo(repo: str) -> str | None:
    """Return ``repo`` with the retired root swapped for the new one, or None."""
    for legacy_root, new_root in (
        (_LEGACY_ROOT_TILDE, _NEW_ROOT_TILDE),
        (os.path.expanduser(_LEGACY_ROOT_TILDE), os.path.expanduser(_NEW_ROOT_TILDE)),
    ):
        legacy_prefix = legacy_root + "/"
        if repo.startswith(legacy_prefix):
            return new_root + repo[len(legacy_root) :]
    return None


def migrate_skill_source_paths(database_dsn: ConnectSource) -> None:
    """Rewrite retired skill-root prefixes in the DB skill source documents.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187).
    """
    store = SkillSourceStore(database_dsn)
    sources = store.get_sources()
    if sources is None:
        return

    rewritten: dict[str, str] = {}
    skills: dict[str, SkillSourceConfig] = {}
    for skill_key, source in sources.skills.items():
        new_repo = _rewrite_repo(source.repo)
        if new_repo is None:
            skills[skill_key] = source
        else:
            skills[skill_key] = SkillSourceConfig(repo=new_repo, ref=source.ref)
            rewritten[skill_key] = new_repo
    if not rewritten:
        return

    store.put_sources(SkillsConfig(skills=skills))

    dropped: list[str] = []
    lock = store.get_lock()
    if lock is not None:
        dropped = sorted(key for key in lock.skills if key in rewritten)
        if dropped:
            kept = {key: entry for key, entry in lock.skills.items() if key not in rewritten}
            store.put_lock(lock.model_copy(update={"skills": kept}))

    logger.warning(
        "migrated %d skill source(s) off the retired %s root (%s); dropped %d stale "
        "lock ent%s (%s) — rerun `make import-demo` to materialize the repos at the "
        "new location, then relock (`make skills-lock`) or let the first dispatch "
        "re-resolve them",
        len(rewritten),
        _LEGACY_ROOT_TILDE,
        ", ".join(sorted(rewritten)),
        len(dropped),
        "y" if len(dropped) == 1 else "ies",
        ", ".join(dropped),
    )
