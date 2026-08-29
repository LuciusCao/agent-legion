from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services import skill_detail, skill_repo
from server.app.services.job_errors import NotFoundError
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock

_TEXT_EXTENSIONS = skill_repo.TEXT_EXTENSIONS
_MAX_FILE_BYTES = skill_repo.MAX_FILE_BYTES


class SkillCatalogService:
    def __init__(self, database_dsn: ConnectSource, base_dir: Path | None = None) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
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
        # Tag/ref/locked reads resolve to the declared source repo for local
        # path sources (a non-in-place save lands there, not in the cache);
        # URL sources read the cache clone. _skill_dir always runs first: it
        # doubles as the skill-key format/escape guard.
        cache_dir = self._skill_dir(skill_key)
        repo_dir = skill_repo.local_repo_path(source.repo) or cache_dir
        locked = self._lock().skills.get(skill_key)
        return skill_detail.skill_detail(skill_key, source.ref, repo_dir, locked, ref, self._files)

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
