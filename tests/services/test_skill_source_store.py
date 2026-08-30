"""SkillSourceStore (global_settings skill_sources/skill_lock) + startup seed."""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES
from server.app.skills.config import SkillsConfig, SkillsLock
from server.app.skills.seed import seed_skill_sources
from server.app.skills.skill_root_migration import migrate_skill_source_paths
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


def _put_documents(
    sources: dict[str, dict[str, str]],
    lock: dict[str, dict[str, str]],
) -> None:
    store = _store()
    store.put_sources(SkillsConfig.model_validate({"skills": sources}))
    store.put_lock(
        SkillsLock.model_validate(
            {"version": "1", "resolved_at": "2026-08-07T00:00:00Z", "skills": lock}
        )
    )


def test_migration_rewrites_legacy_prefix_and_drops_lock(caplog) -> None:
    _put_documents(
        sources={
            "wf/cap": {"repo": "~/.agents/skills/agent-legion/wf/cap", "ref": "v1"},
        },
        lock={
            "wf/cap": {
                "repo": "~/.agents/skills/agent-legion/wf/cap",
                "ref": "v1",
                "commit": "abc123",
            },
        },
    )

    with caplog.at_level(logging.WARNING, logger="server.app.skills.skill_root_migration"):
        migrate_skill_source_paths(TEST_DATABASE_URL)

    store = _store()
    sources = store.get_sources()
    assert sources is not None
    assert sources.skills["wf/cap"].repo == "~/.agents/skills/wf/cap"
    assert sources.skills["wf/cap"].ref == "v1"
    lock = store.get_lock()
    assert lock is not None
    assert lock.skills == {}
    assert "wf/cap" in caplog.text
    assert "make import-demo" in caplog.text


def test_migration_rewrites_expanded_absolute_form() -> None:
    legacy = str(Path.home() / ".agents" / "skills" / "agent-legion" / "wf" / "cap")
    _put_documents(
        sources={"wf/cap": {"repo": legacy, "ref": "v1"}},
        lock={},
    )

    migrate_skill_source_paths(TEST_DATABASE_URL)

    sources = _store().get_sources()
    assert sources is not None
    assert sources.skills["wf/cap"].repo == str(Path.home() / ".agents" / "skills" / "wf" / "cap")


def test_migration_mixed_old_and_new_only_rewrites_legacy() -> None:
    _put_documents(
        sources={
            "wf/old": {"repo": "~/.agents/skills/agent-legion/wf/old", "ref": "v1"},
            "wf/new": {"repo": "~/.agents/skills/wf/new", "ref": "v2"},
        },
        lock={
            "wf/old": {
                "repo": "~/.agents/skills/agent-legion/wf/old",
                "ref": "v1",
                "commit": "abc123",
            },
            "wf/new": {
                "repo": "~/.agents/skills/wf/new",
                "ref": "v2",
                "commit": "def456",
            },
        },
    )

    migrate_skill_source_paths(TEST_DATABASE_URL)

    store = _store()
    sources = store.get_sources()
    assert sources is not None
    assert sources.skills["wf/old"].repo == "~/.agents/skills/wf/old"
    assert sources.skills["wf/new"].repo == "~/.agents/skills/wf/new"
    lock = store.get_lock()
    assert lock is not None
    assert set(lock.skills) == {"wf/new"}
    assert lock.skills["wf/new"].commit == "def456"


def test_migration_noop_without_legacy_prefix(caplog) -> None:
    # conftest seeded the built-ins: all new-root or remote repos.
    before_sources = _store().get_sources()
    before_lock = _store().get_lock()

    with caplog.at_level(logging.WARNING, logger="server.app.skills.skill_root_migration"):
        migrate_skill_source_paths(TEST_DATABASE_URL)

    assert _store().get_sources() == before_sources
    assert _store().get_lock() == before_lock
    assert "migrated" not in caplog.text


def test_migration_noop_when_no_documents() -> None:
    _clear_documents()

    migrate_skill_source_paths(TEST_DATABASE_URL)

    assert _store().get_sources() is None
    assert _store().get_lock() is None


def test_migration_is_idempotent(caplog) -> None:
    _put_documents(
        sources={"wf/cap": {"repo": "~/.agents/skills/agent-legion/wf/cap", "ref": "v1"}},
        lock={
            "wf/cap": {
                "repo": "~/.agents/skills/agent-legion/wf/cap",
                "ref": "v1",
                "commit": "abc123",
            },
        },
    )

    migrate_skill_source_paths(TEST_DATABASE_URL)
    after_sources = _store().get_sources()
    after_lock = _store().get_lock()

    # caplog.text 覆盖整个测试已捕获的记录：清掉第一次调用的 warning，
    # 只断言第二次调用是真的 no-op。
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="server.app.skills.skill_root_migration"):
        migrate_skill_source_paths(TEST_DATABASE_URL)

    assert _store().get_sources() == after_sources
    assert _store().get_lock() == after_lock
    assert "migrated" not in caplog.text
