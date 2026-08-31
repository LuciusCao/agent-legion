from __future__ import annotations

from pathlib import Path

from server.app.skills.manager import SkillManager
from tests.helpers.skill_git import _make_skill_repo
from tests.helpers.skill_store import memory_skill_store


def _make_skill_manager(
    tmp_path: Path,
    skill_key: str,
    validate_script: str | None = None,
) -> SkillManager:
    """Create a SkillManager backed by an in-place git repo for the given skill."""
    _make_skill_repo(tmp_path / "skills", skill_key, validate_script=validate_script)
    return SkillManager(
        store=memory_skill_store(),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
