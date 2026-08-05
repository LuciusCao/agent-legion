"""Skill path validation and tag discovery for the Studio Agent editor.

A skill is a directory under the managed skills base dir
(``~/.agents/skills/agent-legion``) containing a ``SKILL.md``; each skill
directory is its own git repository whose tags are the selectable refs
(``config/skills.lock`` stays the authority on which ref is pinned — the
validator only reports what exists, it never mutates the lock).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_GIT_TAG_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class SkillValidation:
    valid: bool
    path: str
    skill_key: str | None = None
    error: str | None = None
    tags: tuple[str, ...] = ()
    latest_tag: str | None = None
    locked_ref: str | None = None


@dataclass(frozen=True)
class SkillTags:
    path: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    latest_tag: str | None = None


class SkillValidator:
    """Validate skill directories and list their git tags (latest first)."""

    def __init__(self, base_dir: Path, lock_path: Path | None = None) -> None:
        self._base_dir = base_dir.expanduser()
        self._lock_path = lock_path

    def validate(self, raw_path: str) -> SkillValidation:
        path, error = self._resolve_inside_base(raw_path)
        if error is not None:
            return SkillValidation(valid=False, path=raw_path, error=error)
        assert path is not None
        if not path.is_dir():
            return SkillValidation(
                valid=False, path=str(path), error="skill path is not a directory"
            )
        if not (path / "SKILL.md").is_file():
            return SkillValidation(
                valid=False, path=str(path), error="skill directory must contain SKILL.md"
            )
        skill_key = path.relative_to(self._base_dir).as_posix()
        tags = self._git_tags(path)
        return SkillValidation(
            valid=True,
            path=str(path),
            skill_key=skill_key,
            tags=tags,
            latest_tag=tags[0] if tags else None,
            locked_ref=self._locked_ref(skill_key),
        )

    def list_tags(self, raw_path: str) -> SkillTags:
        path, error = self._resolve_inside_base(raw_path)
        if error is not None or path is None or not path.is_dir():
            return SkillTags(path=raw_path)
        tags = self._git_tags(path)
        return SkillTags(path=str(path), tags=tags, latest_tag=tags[0] if tags else None)

    def _resolve_inside_base(self, raw_path: str) -> tuple[Path | None, str | None]:
        if not raw_path or not raw_path.strip():
            return None, "skill path is required"
        candidate = Path(raw_path.strip()).expanduser()
        if not candidate.is_absolute():
            return None, "skill path must be absolute"
        resolved = candidate.resolve()
        base = self._base_dir.resolve()
        if resolved != base and base not in resolved.parents:
            return None, f"skill path must be inside the managed skills dir: {base}"
        return resolved, None

    @staticmethod
    def _git_tags(path: Path) -> tuple[str, ...]:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "tag", "--list", "--sort=-version:refname"],
                capture_output=True,
                text=True,
                timeout=_GIT_TAG_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if result.returncode != 0:
            # Not a git repository (or git unusable): tags are optional.
            return ()
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())

    def _locked_ref(self, skill_key: str) -> str | None:
        if self._lock_path is None or not self._lock_path.is_file():
            return None
        try:
            raw = yaml.safe_load(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(raw, dict):
            return None
        skills = raw.get("skills")
        if not isinstance(skills, dict):
            return None
        entry = skills.get(skill_key)
        if not isinstance(entry, dict):
            return None
        ref = entry.get("ref")
        return str(ref) if ref else None
