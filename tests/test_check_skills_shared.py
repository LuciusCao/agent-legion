from __future__ import annotations

import importlib
from pathlib import Path

import pytest

check_skills_shared = importlib.import_module("scripts.check-skills-shared")

pytestmark = pytest.mark.no_db


def test_resolve_local_repo_file_url(tmp_path: Path) -> None:
    repo = tmp_path / "skill"
    assert check_skills_shared._resolve_local_repo(f"file://{repo}") == repo.resolve()


def test_resolve_local_repo_tilde_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    resolved = check_skills_shared._resolve_local_repo("~/.agents/skills/agent-legion/wf/cap")
    assert resolved == (fake_home / ".agents" / "skills" / "agent-legion" / "wf" / "cap").resolve()


def test_resolve_local_repo_absolute_path(tmp_path: Path) -> None:
    assert check_skills_shared._resolve_local_repo(str(tmp_path)) == tmp_path.resolve()


@pytest.mark.parametrize(
    "repo",
    [
        "https://example.com/skill.git",
        "git@example.com:skill.git",
        "relative/path",
    ],
)
def test_resolve_local_repo_rejects_remote_and_relative(repo: str) -> None:
    assert check_skills_shared._resolve_local_repo(repo) is None
