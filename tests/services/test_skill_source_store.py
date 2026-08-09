"""SkillSourceStore (global_settings skill_sources/skill_lock) + startup seed."""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES
from server.app.skills.config import SkillsConfig, SkillsLock
from server.app.skills.seed import seed_skill_sources
from tests.postgres_support import TEST_DATABASE_URL


def _store() -> SkillSourceStore:
    return SkillSourceStore(TEST_DATABASE_URL)


def test_store_round_trip_sources_and_lock() -> None:
    store = _store()
    # conftest seeds the built-in documents after every TRUNCATE; overwrite.
    sources = SkillsConfig.model_validate(
        {"skills": {"wf/cap": {"repo": "https://example.com/s.git", "ref": "main"}}}
    )
    lock = SkillsLock.model_validate(
        {
            "skills": {
                "wf/cap": {"repo": "https://example.com/s.git", "ref": "main", "commit": "abc123"}
            }
        }
    )

    store.put_sources(sources)
    store.put_lock(lock)

    assert store.get_sources() == sources
    assert store.get_lock() == lock


def _clear_documents() -> None:
    from server.app.db.transaction import write_transaction

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from global_settings where key in ('skill_sources', 'skill_lock')")


def test_seed_seeds_builtin_constants_without_legacy_files(tmp_path: Path) -> None:
    _clear_documents()
    seed_skill_sources(TEST_DATABASE_URL, tmp_path)

    store = _store()
    assert store.get_sources() == BUILTIN_SKILL_SOURCES
    assert store.get_lock() == BUILTIN_SKILL_LOCK


def test_seed_imports_legacy_files_once(tmp_path: Path, caplog) -> None:
    _clear_documents()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "skills.yaml").write_text(
        "skills:\n  wf/cap:\n    repo: https://example.com/s.git\n    ref: v9\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "skills.lock").write_text(
        "version: '1'\n"
        "skills:\n"
        "  wf/cap:\n"
        "    repo: https://example.com/s.git\n"
        "    ref: v9\n"
        "    commit: deadbeef\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="server.app.skills.seed"):
        seed_skill_sources(TEST_DATABASE_URL, tmp_path)

    store = _store()
    sources = store.get_sources()
    assert sources is not None
    assert sources.skills["wf/cap"].ref == "v9"
    lock = store.get_lock()
    assert lock is not None
    assert lock.skills["wf/cap"].commit == "deadbeef"
    assert "never read again" in caplog.text


def test_seed_is_noop_when_documents_exist(tmp_path: Path) -> None:
    # conftest already seeded the built-ins; a legacy file must be ignored.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "skills.yaml").write_text(
        "skills:\n  wf/cap:\n    repo: https://example.com/s.git\n    ref: v9\n",
        encoding="utf-8",
    )

    seed_skill_sources(TEST_DATABASE_URL, tmp_path)

    assert _store().get_sources() == BUILTIN_SKILL_SOURCES
