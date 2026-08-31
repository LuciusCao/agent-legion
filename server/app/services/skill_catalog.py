from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.services import skill_detail, skill_repo
from server.app.services.job_errors import NotFoundError
from server.app.services.skill_lock_store import SkillLockStore
from server.app.skills.config import SkillsLock
from server.app.skills.skill_roots import default_skill_base_dir

_TEXT_EXTENSIONS = skill_repo.TEXT_EXTENSIONS
_MAX_FILE_BYTES = skill_repo.MAX_FILE_BYTES


class SkillCatalogService:
    def __init__(self, database_dsn: ConnectSource, base_dir: Path | None = None) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._store = SkillLockStore(database_dsn)
        self.base_dir = base_dir or default_skill_base_dir()

    def metadata(self, skill_key: str) -> dict[str, str]:
        locked = self._lock().skills.get(skill_key)
        # Without the retired source registry there is no declared default
        # ref, so only a sole pin has an unambiguous "locked version" answer.
        if locked is None or len(locked.refs) != 1:
            return {}
        ref, commit = next(iter(locked.refs.items()))
        return {
            "skill_ref": ref,
            "skill_commit": commit,
        }

    def detail(self, skill_key: str, ref: str | None = None) -> dict[str, Any]:
        # The skill's repo is the in-place directory at <base_dir>/<key>
        # (#322); _skill_dir always runs first: it doubles as the skill-key
        # format/escape guard.
        repo_dir = self._skill_dir(skill_key)
        return skill_detail.skill_detail(skill_key, repo_dir, ref, self._files)

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

    def _lock(self) -> SkillsLock:
        return self._store.get_lock() or SkillsLock()
