"""Startup migration retiring the global_settings skill_sources document (#322)."""

from __future__ import annotations

import logging

from server.app.jobs.queries.global_settings import global_settings_kv_from_dsn
from server.app.skills.skill_sources_retirement import retire_skill_sources_document
from tests.postgres_support import TEST_DATABASE_URL


def test_retirement_deletes_the_sources_document_and_keeps_the_lock() -> None:
    kv = global_settings_kv_from_dsn(TEST_DATABASE_URL)
    kv.put_global_settings_document("skill_sources", {"skills": {"wf/cap": {"repo": "r"}}})
    kv.put_global_settings_document(
        "skill_lock", {"skills": {"wf/cap": {"repo": "r", "refs": {"v1": "a" * 40}}}}
    )

    retire_skill_sources_document(TEST_DATABASE_URL)

    assert kv.get_global_settings_document("skill_sources") is None
    # The lock is deliberately preserved: pinned tag refs stay frozen.
    assert kv.get_global_settings_document("skill_lock") == {
        "skills": {"wf/cap": {"repo": "r", "refs": {"v1": "a" * 40}}}
    }


def test_retirement_logs_only_when_it_deletes(caplog) -> None:
    kv = global_settings_kv_from_dsn(TEST_DATABASE_URL)
    logger = "server.app.skills.skill_sources_retirement"

    with caplog.at_level(logging.WARNING, logger=logger):
        retire_skill_sources_document(TEST_DATABASE_URL)
    assert caplog.records == []

    kv.put_global_settings_document("skill_sources", {"skills": {}})
    with caplog.at_level(logging.WARNING, logger=logger):
        retire_skill_sources_document(TEST_DATABASE_URL)
        # Second run is a silent no-op (idempotent).
        retire_skill_sources_document(TEST_DATABASE_URL)
    assert len(caplog.records) == 1
    assert "skill_sources" in caplog.records[0].getMessage()
    assert kv.get_global_settings_document("skill_sources") is None
