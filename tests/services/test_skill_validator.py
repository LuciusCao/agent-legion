"""SkillValidator: skill path validation and git tag discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.app.services.skill_validator import SkillValidator

pytestmark = pytest.mark.no_db


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_skill(base: Path, key: str, tags: list[str] | None = None) -> Path:
    skill_dir = base / key
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    if tags is not None:
        _git(skill_dir, "init", "-q")
        _git(skill_dir, "-c", "user.email=t@t", "-c", "user.name=t", "add", "SKILL.md")
        _git(
            skill_dir,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        )
        for tag in tags:
            _git(skill_dir, "tag", tag)
    return skill_dir


@pytest.fixture
def base_dir(tmp_path):
    return tmp_path / "skills"


def test_validate_happy_path_with_tags(base_dir, tmp_path) -> None:
    skill_dir = _make_skill(base_dir, "wf/review", tags=["v1.0.0", "v1.10.0", "v1.2.0"])
    lock = tmp_path / "skills.lock"
    lock.write_text("skills:\n  wf/review:\n    ref: v1.2.0\n", encoding="utf-8")

    result = SkillValidator(base_dir, lock).validate(str(skill_dir))

    assert result.valid is True
    assert result.skill_key == "wf/review"
    # version:refname sort keeps semver order, latest first.
    assert result.tags == ("v1.10.0", "v1.2.0", "v1.0.0")
    assert result.latest_tag == "v1.10.0"
    assert result.locked_ref == "v1.2.0"


def test_validate_expands_home(monkeypatch, base_dir, tmp_path) -> None:
    home = tmp_path / "home"
    base = home / ".agents" / "skills"
    _make_skill(base, "wf/review")
    monkeypatch.setenv("HOME", str(home))

    result = SkillValidator(base).validate("~/.agents/skills/wf/review")

    assert result.valid is True
    assert result.skill_key == "wf/review"
    assert result.tags == ()  # not a git repo: tags are optional


def test_validate_rejects_bad_paths(base_dir, tmp_path) -> None:
    validator = SkillValidator(base_dir)
    assert validator.validate("").valid is False
    assert validator.validate("relative/path").valid is False
    outside = validator.validate(str(tmp_path / "elsewhere"))
    assert outside.valid is False
    assert "managed skills dir" in (outside.error or "")


def test_validate_requires_directory_and_skill_md(base_dir) -> None:
    base_dir.mkdir(parents=True)
    validator = SkillValidator(base_dir)

    missing = validator.validate(str(base_dir / "wf" / "nope"))
    assert missing.valid is False
    assert "not a directory" in (missing.error or "")

    no_md = base_dir / "wf" / "plain"
    no_md.mkdir(parents=True)
    result = validator.validate(str(no_md))
    assert result.valid is False
    assert "SKILL.md" in (result.error or "")


def test_list_tags(base_dir) -> None:
    skill_dir = _make_skill(base_dir, "wf/review", tags=["v0.1", "v0.2"])
    validator = SkillValidator(base_dir)

    tags = validator.list_tags(str(skill_dir))
    assert tags.tags == ("v0.2", "v0.1")
    assert tags.latest_tag == "v0.2"

    empty = validator.list_tags(str(base_dir / "wf" / "missing"))
    assert empty.tags == ()
    assert empty.latest_tag is None
