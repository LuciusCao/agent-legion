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
from tests.helpers.skill_store import memory_skill_store


def _make_manager(
    tmp_path: Path,
    declared_key: str | None = "question_comprehension_info/generate_key_info",
) -> SkillManager:
    skills = (
        {declared_key: {"repo": "file:///nonexistent/repo.git", "ref": "main"}}
        if declared_key
        else {}
    )

    base_dir = tmp_path / "skills"
    base_dir.mkdir(parents=True, exist_ok=True)

    return SkillManager(
        store=memory_skill_store(skills),
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

    symlink = manager.base_dir / "question_comprehension_info"
    symlink.symlink_to(outside)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("question_comprehension_info/generate_key_info", str(uuid.uuid4()))

    # The outside target must remain untouched (no git clone/checkout happened).
    assert sentinel.read_text(encoding="utf-8") == "outside"


@pytest.mark.full_gate
def test_absolute_skill_key_rejected(tmp_path: Path) -> None:
    """Absolute skill keys must be rejected before any filesystem mutation."""
    manager = _make_manager(tmp_path)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("/question_comprehension_info/generate_key_info", str(uuid.uuid4()))


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

    cache_dir = manager.base_dir / "question_comprehension_info" / "extract_keywords"
    cache_dir.mkdir(parents=True)
    (cache_dir / "SKILL.md").write_text("not a repo\n", encoding="utf-8")

    with pytest.raises(SkillRepoError):
        manager.get_skill_dir("question_comprehension_info/generate_key_info", str(uuid.uuid4()))


@pytest.mark.full_gate
def test_destructive_git_ops_only_after_path_validation(tmp_path: Path) -> None:
    """Path validation must run before any directory creation or git invocation."""
    manager = _make_manager(tmp_path)

    before = set(manager.base_dir.rglob("*"))

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("/question_comprehension_info/generate_key_info", str(uuid.uuid4()))

    after = set(manager.base_dir.rglob("*"))
    assert after == before


@pytest.mark.full_gate
def test_symlink_run_dir_escape_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An intermediate symlink in runs_dir must not redirect rmtree outside."""
    manager = _make_manager(tmp_path)
    execution_id = str(uuid.uuid4())
    cache_dir = manager.base_dir / "question_comprehension_info" / "extract_keywords"
    cache_dir.mkdir(parents=True)
    (cache_dir / "SKILL.md").write_text("# cached\n", encoding="utf-8")
    monkeypatch.setattr(manager, "_ensure_cached", lambda *_args, **_kwargs: None)

    outside = tmp_path / "outside_runs"
    capability = outside / "question_comprehension_info" / "extract_keywords"
    capability.mkdir(parents=True)
    sentinel = capability / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")
    manager.runs_dir.mkdir(parents=True)
    (manager.runs_dir / execution_id).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillPathError):
        manager.get_skill_dir("question_comprehension_info/generate_key_info", execution_id)

    assert sentinel.read_text(encoding="utf-8") == "outside"
