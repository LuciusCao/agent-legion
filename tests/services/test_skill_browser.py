"""SkillBrowser: candidate skill directory listing (#327)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.services.skill_browser import SkillBrowser

pytestmark = pytest.mark.no_db


def test_lists_directories_sorted_files_excluded(tmp_path) -> None:
    scope = tmp_path / "ws-1"
    (scope / "write-script").mkdir(parents=True)
    (scope / "generate-questions").mkdir()
    (scope / "review-questions").mkdir()
    (scope / "notes.txt").write_text("not a dir", encoding="utf-8")

    assert SkillBrowser(tmp_path).list_directories("ws-1") == (
        "generate-questions",
        "review-questions",
        "write-script",
    )


def test_scopes_are_isolated(tmp_path) -> None:
    (tmp_path / "ws-1" / "write-script").mkdir(parents=True)
    (tmp_path / "ws-2" / "generate-questions").mkdir(parents=True)

    browser = SkillBrowser(tmp_path)
    assert browser.list_directories("ws-2") == ("generate-questions",)


def test_missing_scope_dir_returns_empty(tmp_path) -> None:
    assert SkillBrowser(tmp_path).list_directories("ws-nope") == ()


def test_rejects_empty_and_blank_scope(tmp_path) -> None:
    browser = SkillBrowser(tmp_path)
    assert browser.list_directories("") == ()
    assert browser.list_directories("   ") == ()


def test_rejects_traversal_and_absolute_scope(tmp_path) -> None:
    base = tmp_path / "skills"
    (tmp_path / "outside-skill").mkdir()
    base.mkdir()

    browser = SkillBrowser(base)
    assert browser.list_directories("..") == ()
    assert browser.list_directories(str(tmp_path)) == ()


def test_rejects_symlink_escaping_base(tmp_path) -> None:
    base = tmp_path / "skills"
    base.mkdir()
    outside = tmp_path / "outside"
    (outside / "secret-skill").mkdir(parents=True)
    (base / "link").symlink_to(outside, target_is_directory=True)

    assert SkillBrowser(base).list_directories("link") == ()


def test_expands_user_in_base_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = tmp_path / "skills" / "ws-1"
    (scope / "write-script").mkdir(parents=True)

    assert SkillBrowser(Path("~/skills")).list_directories("ws-1") == ("write-script",)
