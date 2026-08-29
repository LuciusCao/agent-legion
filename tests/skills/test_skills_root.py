"""Skills-root location helpers (``server.app.skills.skill_roots``).

The skills root is the single source of truth for the skill cache base dir:
SkillManager, the catalog/editing services and the relock CLI all default to
it, and workspace agent skills live at ``<root>/<workspace_id>/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.skills import skill_roots as paths

pytestmark = pytest.mark.no_db


def test_skills_root_lives_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    assert paths.skills_root() == fake_home / ".agents" / "skills"


def test_default_skill_base_dir_is_the_skills_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert paths.default_skill_base_dir() == paths.skills_root()


def test_workspace_skill_dir_nests_under_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    assert paths.workspace_skill_dir("demo-ws") == fake_home / ".agents" / "skills" / "demo-ws"


def test_display_constants() -> None:
    assert paths.SKILLS_ROOT_DISPLAY == "~/.agents/skills"
    assert paths.workspace_skill_prefix_display("demo-ws") == "~/.agents/skills/demo-ws/"
