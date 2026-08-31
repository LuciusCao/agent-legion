"""Relock support: re-resolve the refs already pinned in the skill lock.

Only the relock flow (``server.app.skills.lock.refresh_lock``) uses this;
runtime dispatch goes through ``SkillManager._resolve_pinned`` instead. Since
#322 there is no source registry to iterate: relock refreshes exactly the
refs the lock already froze (``latest`` is never one of them — it is never
locked), each resolved against the in-place repo at ``<skills root>/<key>``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.skills.manager import SkillManager

logger = logging.getLogger(__name__)


def refresh_pinned_refs(
    manager: SkillManager,
    skill_key: str,
    cache_dir: Path,
    pinned_refs: dict[str, str],
) -> dict[str, str]:
    """Re-resolve every pinned ref in ``cache_dir`` to its current commit."""
    # Pure read-only resolution (#330): the working tree is never moved; the
    # next dispatch materializes whatever commit its own ref resolves to.
    manager._require_cache_dir(skill_key, cache_dir)
    refs = {ref: manager._rev_parse(cache_dir, ref) for ref in sorted(pinned_refs)}
    logger.info("Refreshed Pi skill %s pins: %s", skill_key, sorted(refs))
    return refs
