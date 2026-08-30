"""Skills-root location helpers (``server.app.skills.skill_roots``).

The skills root is the single source of truth for the skill cache base dir:
SkillManager, the catalog/editing services and the relock CLI all default to
it, and workspace agent skills live at ``<root>/<workspace_id>/``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_editing import SkillEditingService
from server.app.skills import lock as lock_cli
from server.app.skills import skill_roots as paths
from server.app.skills.runtime import build_skill_manager
from tests.helpers.skill_store import memory_skill_store

pytestmark = pytest.mark.no_db


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    return home


def test_skills_root_lives_under_home(fake_home: Path) -> None:
    assert paths.skills_root() == fake_home / ".agents" / "skills"


def test_default_skill_base_dir_is_the_skills_root(fake_home: Path) -> None:
    assert paths.default_skill_base_dir() == paths.skills_root()


def test_workspace_skill_dir_nests_under_root(fake_home: Path) -> None:
    assert paths.workspace_skill_dir("demo-ws") == fake_home / ".agents" / "skills" / "demo-ws"


def test_display_constants() -> None:
    assert paths.SKILLS_ROOT_DISPLAY == "~/.agents/skills"
    assert paths.workspace_skill_prefix_display("demo-ws") == "~/.agents/skills/demo-ws/"


@pytest.mark.parametrize(
    "bad_id",
    ["", "..", "a/../b", "/abs/path", "a/b", "a\\b", "Upper", "sp ace", "-lead", "x" * 65],
)
def test_workspace_skill_dir_rejects_unsafe_ids(bad_id: str) -> None:
    with pytest.raises(ValueError, match="workspace id"):
        paths.workspace_skill_dir(bad_id)
    with pytest.raises(ValueError, match="workspace id"):
        paths.workspace_skill_prefix_display(bad_id)


def test_build_skill_manager_defaults_to_skills_root(fake_home: Path) -> None:
    manager = build_skill_manager("postgresql://unused")
    assert manager.base_dir == paths.skills_root()


def test_skill_catalog_defaults_to_skills_root(fake_home: Path) -> None:
    service = SkillCatalogService("postgresql://unused")
    assert service.base_dir == paths.skills_root()


def test_skill_editing_defaults_to_skills_root(fake_home: Path) -> None:
    service = SkillEditingService(store=memory_skill_store({}))
    assert service.base_dir == paths.skills_root()


def test_relock_cli_defaults_to_skills_root(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    captured: dict[str, Path] = {}
    settings = SimpleNamespace(database_url="postgresql://unused", skills_runs_dir=None)
    monkeypatch.setattr(lock_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(lock_cli, "SkillSourceStore", lambda dsn: object())
    monkeypatch.setattr(
        lock_cli,
        "refresh_lock",
        lambda store, base_dir, runs_dir=None: captured.setdefault("base_dir", base_dir),
    )
    monkeypatch.setattr(sys, "argv", ["lock"])

    lock_cli.main()

    assert captured["base_dir"] == paths.skills_root()
