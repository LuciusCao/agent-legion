from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.app.skills.config import LockedSkillSource
from server.app.skills.lock import main, refresh_lock


def test_refresh_lock_passes_resolved_sources_to_write(tmp_path: Path) -> None:
    config_path = tmp_path / "skills.yaml"
    lock_path = tmp_path / "skills.lock"
    base_dir = tmp_path / "skills"

    config_path.write_text(
        "skills:\n  wf/cap: {repo: https://example.com/skill.git, ref: main}\n",
        encoding="utf-8",
    )

    mock_source = LockedSkillSource(
        repo="https://example.com/skill.git",
        ref="main",
        commit="abc123",
    )

    with patch("server.app.skills.lock.SkillManager") as mock_manager_cls:
        manager = MagicMock()
        manager._load_config.return_value.skills = {"wf/cap": MagicMock()}
        manager._parse_skill_key.return_value = ("wf", "cap")
        manager._resolve_cache_dir.return_value = tmp_path / "cache"
        manager._refresh_source.return_value = mock_source
        mock_manager_cls.return_value = manager

        refresh_lock(config_path, lock_path, base_dir)

    manager._write_lock_unlocked.assert_called_once()
    written_lock = manager._write_lock_unlocked.call_args[0][0]
    assert written_lock.skills["wf/cap"].commit == "abc123"


def test_main_invokes_refresh_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "skills.yaml"
    lock_path = tmp_path / "skills.lock"
    base_dir = tmp_path / "skills"

    config_path.write_text("skills: {}\n", encoding="utf-8")

    called = {}

    def fake_refresh(config: Path, lock: Path, base: Path) -> None:
        called["config"] = config
        called["lock"] = lock
        called["base"] = base

    monkeypatch.setattr("server.app.skills.lock.refresh_lock", fake_refresh)
    monkeypatch.setattr(
        "sys.argv",
        [
            "refresh-lock",
            "--config",
            str(config_path),
            "--lock",
            str(lock_path),
            "--base-dir",
            str(base_dir),
        ],
    )

    main()

    assert called["config"] == config_path
    assert called["lock"] == lock_path
    assert called["base"] == base_dir
