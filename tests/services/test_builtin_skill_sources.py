"""Structural guard for the built-in skill source/lock seed constants.

The tracked ``config/skills.yaml`` / ``config/skills.lock`` were transcribed
into ``server.app.skills.builtin_sources`` (field-by-field equivalence was
verified against the tracked files before their deletion) and are seeded into
``global_settings`` when the DB has no ``skill_sources`` row. This test pins
the constants' internal consistency: every declared skill is locked at a
full commit, and sources/lock agree on repo+ref per skill.

Exception: the four demo skills (``education-video-problems-generation/*``)
have no lock entry — their repos are created per machine by
``make import-demo``, so the commit cannot be pinned in a tracked constant;
the first dispatch or relock resolves and locks them.
"""

from __future__ import annotations

import re

import pytest

from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES

pytestmark = pytest.mark.no_db

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DEMO_PREFIX = "education-video-problems-generation/"


def _demo_keys() -> set[str]:
    return {key for key in BUILTIN_SKILL_SOURCES.skills if key.startswith(_DEMO_PREFIX)}


def test_builtin_sources_cover_demo_skills() -> None:
    assert _demo_keys() == {
        f"{_DEMO_PREFIX}{name}"
        for name in ("write-script", "review-script", "generate-questions", "review-questions")
    }
    assert len(BUILTIN_SKILL_SOURCES.skills) == 4
    # Demo skills are intentionally unlocked (machine-local repos); the lock
    # starts empty and the first relock fills it.
    assert set(BUILTIN_SKILL_LOCK.skills) == set(BUILTIN_SKILL_SOURCES.skills) - _demo_keys()


def test_demo_sources_point_at_import_demo_repos() -> None:
    for key in _demo_keys():
        source = BUILTIN_SKILL_SOURCES.skills[key]
        assert source.repo == f"~/.agents/skills/agent-legion/{key}"
        assert source.ref == "v1.0.0"


def test_builtin_lock_is_consistent_with_sources() -> None:
    for key, locked in BUILTIN_SKILL_LOCK.skills.items():
        source = BUILTIN_SKILL_SOURCES.skills[key]
        assert locked.repo == source.repo
        assert locked.ref == source.ref
        assert _COMMIT_RE.match(locked.commit), f"{key} commit must be a full git sha"


def test_builtin_lock_metadata_matches_retired_file() -> None:
    assert BUILTIN_SKILL_LOCK.version == "1"
    assert BUILTIN_SKILL_LOCK.resolved_at == "2026-08-07T01:44:10Z"
