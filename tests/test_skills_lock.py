from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from server.app.skills.config import LockedSkillSource
from server.app.skills.lock import main, refresh_lock
from tests.helpers.skill_store import memory_skill_store


def test_refresh_lock_passes_resolved_sources_to_write(tmp_path: Path) -> None:
    base_dir = tmp_path / "skills"
    store = memory_skill_store({"wf/cap": {"repo": "https://example.com/skill.git", "ref": "main"}})

    mock_source = LockedSkillSource(
        repo="https://example.com/skill.git",
        ref="main",
        commit="abc123",
    )

    with (
        patch("server.app.skills.lock.SkillManager") as mock_manager_cls,
        patch("server.app.skills.lock.refresh_source", return_value=mock_source),
    ):
        manager = MagicMock()
        manager._load_config.return_value.skills = {"wf/cap": MagicMock()}
        manager._parse_skill_key.return_value = ("wf", "cap")
        manager._resolve_cache_dir.return_value = tmp_path / "cache"
        mock_manager_cls.return_value = manager

        refresh_lock(store, base_dir)

    manager._write_lock_unlocked.assert_called_once()
    written_lock = manager._write_lock_unlocked.call_args[0][0]
    assert written_lock.skills["wf/cap"].commit == "abc123"


def test_main_invokes_refresh_lock(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "skills"

    called = {}

    def fake_refresh(store, base: Path) -> None:
        called["store"] = store
        called["base"] = base

    monkeypatch.setattr("server.app.skills.lock.refresh_lock", fake_refresh)
    monkeypatch.setattr("server.app.skills.lock._default_dsn", lambda: "postgresql://t/t")
    monkeypatch.setattr(
        "sys.argv",
        [
            "refresh-lock",
            "--base-dir",
            str(base_dir),
        ],
    )

    main()

    assert called["store"]._dsn == "postgresql://t/t"
    assert called["base"] == base_dir


def test_main_honors_database_url_override(tmp_path: Path, monkeypatch) -> None:
    called = {}

    def fake_refresh(store, base: Path) -> None:
        called["store"] = store

    monkeypatch.setattr("server.app.skills.lock.refresh_lock", fake_refresh)
    monkeypatch.setattr(
        "sys.argv",
        ["refresh-lock", "--database-url", "postgresql://example/db"],
    )

    main()

    assert called["store"]._dsn == "postgresql://example/db"
