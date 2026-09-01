"""Startup migration retiring the ``skill_sources`` registry document (#322).

The global skill source registry (repo + default ref per skill, stored in
``global_settings`` under the ``skill_sources`` key) is retired: a skill's
location derives from the skills root + key (in-place repo at
``~/.agents/skills/<group>/<name>``), and an empty/``latest`` node ref
follows the repo's live HEAD instead of the registry's default ref. This
migration deletes the persisted document so upgraded deployments stop
carrying a dead registry.

Runs once per process in ``create_app``. Idempotent: with no ``skill_sources``
row there is nothing to delete and the migration is a no-op (no log). The
``skill_lock`` document is deliberately kept — pinned tag refs stay frozen.
"""

from __future__ import annotations

import logging

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)

logger = logging.getLogger(__name__)

SOURCES_KEY = "skill_sources"


def retire_skill_sources_document(database_dsn: ConnectSource) -> None:
    """Delete the retired ``skill_sources`` document when present.

    ``database_dsn`` accepts the JobQueries facade or a bare DSN string
    (BOUNDARY-DATA-001, #187).
    """
    kv: GlobalSettingsKVQueriesMixin = (
        global_settings_kv_from_dsn(database_dsn) if isinstance(database_dsn, str) else database_dsn
    )
    if kv.delete_global_settings_document(SOURCES_KEY):
        logger.warning(
            "deleted the retired global_settings %s document (#322): skill locations "
            "derive from the skills root + key, and unpinned node refs now follow "
            "each skill repo's live HEAD (pin a tag on the node to freeze a version)",
            SOURCES_KEY,
        )
