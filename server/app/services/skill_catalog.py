from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from server.app.services.job_errors import NotFoundError
from server.app.skills.config import SkillsConfig, SkillsLock

_TEXT_EXTENSIONS = {".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
_MAX_FILE_BYTES = 128 * 1024


class SkillCatalogService:
    def __init__(self, root_dir: Path, base_dir: Path | None = None) -> None:
        self.config_path = root_dir / "config" / "skills.yaml"
        self.lock_path = root_dir / "config" / "skills.lock"
        self.base_dir = base_dir or Path.home() / ".agents" / "skills" / "agent-legion"

    def metadata(self, skill_key: str) -> dict[str, str]:
        source = self._config().skills.get(skill_key)
        if source is None:
            return {}
        locked = self._lock().skills.get(skill_key)
        return {
            "skill_ref": source.ref,
            "skill_commit": locked.commit if locked is not None else "",
        }

    def detail(self, skill_key: str) -> dict[str, Any]:
        source = self._config().skills.get(skill_key)
        if source is None:
            raise NotFoundError(f"Skill {skill_key!r} is not configured")
        skill_dir = self._skill_dir(skill_key)
        locked = self._lock().skills.get(skill_key)
        return {
            "key": skill_key,
            "ref": source.ref,
            "commit": locked.commit if locked is not None else "",
            "available": skill_dir.is_dir(),
            "files": self._files(skill_dir) if skill_dir.is_dir() else [],
        }

    def _skill_dir(self, skill_key: str) -> Path:
        parts = skill_key.split("/")
        if len(parts) != 2 or not all(parts) or ".." in parts:
            raise NotFoundError("Invalid skill key")
        root = self.base_dir.resolve()
        candidate = (root / parts[0] / parts[1]).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise NotFoundError("Invalid skill path") from exc
        return candidate

    def _files(self, skill_dir: Path) -> list[dict[str, Any]]:
        candidates = [skill_dir / "SKILL.md"]
        for folder in (skill_dir / "references", skill_dir / "scripts"):
            if folder.is_dir():
                candidates.extend(sorted(folder.rglob("*")))
        files: list[dict[str, Any]] = []
        for path in candidates:
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in _TEXT_EXTENSIONS
            ):
                continue
            size = path.stat().st_size
            raw = path.read_bytes()[:_MAX_FILE_BYTES]
            files.append(
                {
                    "path": path.relative_to(skill_dir).as_posix(),
                    "size": size,
                    "content": raw.decode("utf-8", errors="replace"),
                    "truncated": size > _MAX_FILE_BYTES,
                }
            )
        return files

    def _config(self) -> SkillsConfig:
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return SkillsConfig.model_validate(raw)

    def _lock(self) -> SkillsLock:
        if not self.lock_path.is_file():
            return SkillsLock()
        raw = yaml.safe_load(self.lock_path.read_text(encoding="utf-8")) or {}
        return SkillsLock.model_validate(raw)
