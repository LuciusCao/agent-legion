from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from server.app.skills.lock import main, refresh_lock
from tests.helpers.skill_store import memory_skill_store


def test_refresh_lock_passes_resolved_pins_to_write(tmp_path: Path) -> None:
    base_dir = tmp_path / "skills"
    store = memory_skill_store(
        lock={"skills": {"wf/cap": {"repo": "stale", "refs": {"v1": "0" * 40}}}}
    )

    with (
        patch("server.app.skills.lock.SkillManager") as mock_manager_cls,
        patch("server.app.skills.lock.refresh_pinned_refs", return_value={"v1": "abc123"}),
    ):
        manager = MagicMock()
        manager._parse_skill_key.return_value = ("wf", "cap")
        manager._resolve_cache_dir.return_value = tmp_path / "cache"
        mock_manager_cls.return_value = manager

        refresh_lock(store, base_dir)

    manager._write_lock_unlocked.assert_called_once()
    written_lock = manager._write_lock_unlocked.call_args[0][0]
    assert written_lock.skills["wf/cap"].refs == {"v1": "abc123"}
    assert written_lock.skills["wf/cap"].repo == str(tmp_path / "cache")


def test_refresh_lock_without_entries_writes_an_empty_lock(tmp_path: Path) -> None:
    """#322: with no source registry, an empty lock refreshes to an empty
    lock (there is nothing to iterate)."""
    store = memory_skill_store(lock={})

    refresh_lock(store, tmp_path / "skills")

    lock = store.get_lock()
    assert lock is not None
    assert lock.skills == {}


def test_main_invokes_refresh_lock(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "skills"
    runs_dir = tmp_path / "runs"

    called = {}

    def fake_refresh(store, base: Path, runs_dir: Path | None = None) -> None:
        called["store"] = store
        called["base"] = base
        called["runs_dir"] = runs_dir

    monkeypatch.setattr("server.app.skills.lock.refresh_lock", fake_refresh)

    class _FakeSettings:
        database_url = "postgresql://t/t"
        skills_runs_dir = runs_dir

    monkeypatch.setattr(
        "server.app.skills.lock.load_settings",
        lambda: _FakeSettings(),
    )
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
    assert called["runs_dir"] == runs_dir


def test_main_honors_database_url_override(tmp_path: Path, monkeypatch) -> None:
    called = {}

    def fake_refresh(store, base: Path, runs_dir: Path | None = None) -> None:
        called["store"] = store

    monkeypatch.setattr("server.app.skills.lock.refresh_lock", fake_refresh)

    class _FakeSettings:
        database_url = "postgresql://settings/db"
        skills_runs_dir = tmp_path / "runs"

    monkeypatch.setattr(
        "server.app.skills.lock.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["refresh-lock", "--database-url", "postgresql://example/db"],
    )

    main()

    assert called["store"]._dsn == "postgresql://example/db"
