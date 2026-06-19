"""Full-gate path safety evidence for SkillManager (SECURITY-PATH-001).

Exercises filesystem-level edge cases that unit tests cannot easily cover:

- symlink escape in the skill cache directory tree;
- absolute, empty, and traversal-containing skill keys;
- pre-existing cache directory that is not a git repository;
- assurance that destructive git operations happen only after path validation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from server.app.skills.errors import SkillPathError, SkillRepoError
from server.app.skills.manager import SkillManager


def _make_manager(
    tmp_path: Path,
    declared_key: str | None = "reading_analysis/extract_keywords",
) -> SkillManager:
    config_path = tmp_path / "skills.yaml"
    if declared_key:
        config_path.write_text(
            f"skills:\n  {declared_key}:\n    repo: file:///nonexistent/repo.git\n    ref: main\n"
        )
    else:
        config_path.write_text("skills: {}\n")

    base_dir = tmp_path / "skills"
    base_dir.mkdir(parents=True, exist_ok=True)

    return SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
    )


@pytest.mark.full_gate
def test_symlink_cache_dir_escape_rejected(tmp_path: Path) -> None:
    """A symlink inside base_dir that points outside must be rejected."""
    manager = _make_manager(tmp_path)

    outside = tmp_path / "outside_target"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")

    symlink = manager.base_dir / "reading_analysis"
    symlink.symlink_to(outside)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))

    # The outside target must remain untouched (no git clone/checkout happened).
    assert sentinel.read_text(encoding="utf-8") == "outside"


@pytest.mark.full_gate
def test_absolute_skill_key_rejected(tmp_path: Path) -> None:
    """Absolute skill keys must be rejected before any filesystem mutation."""
    manager = _make_manager(tmp_path)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("/reading_analysis/extract_keywords", str(uuid.uuid4()))


@pytest.mark.full_gate
def test_empty_skill_key_rejected(tmp_path: Path) -> None:
    """Empty skill keys must be rejected before any filesystem mutation."""
    manager = _make_manager(tmp_path)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("", str(uuid.uuid4()))


@pytest.mark.full_gate
def test_existing_nongit_cache_dir_raises(tmp_path: Path) -> None:
    """A pre-existing cache directory that is not a git repo must raise."""
    manager = _make_manager(tmp_path)

    cache_dir = manager.base_dir / "reading_analysis" / "extract_keywords"
    cache_dir.mkdir(parents=True)
    (cache_dir / "SKILL.md").write_text("not a repo\n", encoding="utf-8")

    with pytest.raises(SkillRepoError):
        manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))


@pytest.mark.full_gate
def test_destructive_git_ops_only_after_path_validation(tmp_path: Path) -> None:
    """Path validation must run before any directory creation or git invocation."""
    manager = _make_manager(tmp_path)

    before = set(manager.base_dir.rglob("*"))

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("/reading_analysis/extract_keywords", str(uuid.uuid4()))

    after = set(manager.base_dir.rglob("*"))
    assert after == before
