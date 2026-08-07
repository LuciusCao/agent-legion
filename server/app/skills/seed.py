"""Startup seed for the DB-backed skill sources and skill lock (import-once).

Runs once per process in ``create_app`` (next to the executor definition
seed). When the ``skill_sources`` document is absent from ``global_settings``:

- a legacy tracked ``config/skills.yaml`` still present in the checkout is
  imported into the DB (``config/skills.lock`` likewise; a missing lock file
  falls back to the built-in constants) and a warning is logged — the files
  are a one-way migration channel and are never read again;
- with no legacy files, the built-in constants
  (``server.app.skills.builtin_sources``) are seeded.

When the DB already holds a ``skill_sources`` row the seed is a no-op: the DB
documents are authoritative and the files are ignored.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from server.app.db.connection import DatabaseDsn
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES
from server.app.skills.config import SkillsConfig, SkillsLock

logger = logging.getLogger(__name__)


def seed_skill_sources(database_dsn: DatabaseDsn, root_dir: Path) -> None:
    """Seed-if-absent the skill source documents into ``global_settings``."""
    store = SkillSourceStore(database_dsn)
    if store.get_sources() is not None:
        return

    config_path = root_dir / "config" / "skills.yaml"
    lock_path = root_dir / "config" / "skills.lock"
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        store.put_sources(SkillsConfig.model_validate(raw))
        logger.warning(
            "%s imported into the DB (global_settings skill_sources); skill sources are "
            "now managed through the DB and the file is never read again",
            config_path,
        )
    else:
        store.put_sources(BUILTIN_SKILL_SOURCES.model_copy(deep=True))

    if lock_path.is_file():
        raw = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
        store.put_lock(SkillsLock.model_validate(raw))
        logger.warning(
            "%s imported into the DB (global_settings skill_lock); the skill lock is now "
            "managed through the DB (make skills-lock) and the file is never read again",
            lock_path,
        )
    else:
        store.put_lock(BUILTIN_SKILL_LOCK.model_copy(deep=True))
