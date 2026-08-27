from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.services import skill_repo
from server.app.services.job_errors import NotFoundError
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock

_TEXT_EXTENSIONS = skill_repo.TEXT_EXTENSIONS
_MAX_FILE_BYTES = skill_repo.MAX_FILE_BYTES


class SkillCatalogService:
    def __init__(self, database_dsn: DatabaseDsn, base_dir: Path | None = None) -> None:
        self._store = SkillSourceStore(database_dsn)
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

    def detail(self, skill_key: str, ref: str | None = None) -> dict[str, Any]:
        source = self._config().skills.get(skill_key)
        if source is None:
            raise NotFoundError(f"Skill {skill_key!r} is not configured")
        skill_dir = self._skill_dir(skill_key)
        if ref is not None:
            # Preview a git tag without touching the lock or the checkout.
            return skill_repo.detail_at_ref(skill_key, ref, skill_dir)
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
        return self._store.get_sources() or SkillsConfig()

    def _lock(self) -> SkillsLock:
        return self._store.get_lock() or SkillsLock()
