from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml
from filelock import FileLock

from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError

logger = logging.getLogger(__name__)


class SkillManager:
    def __init__(
        self,
        config_path: Path,
        lock_path: Path,
        base_dir: Path,
        runs_dir: Path | None = None,
        git_command: list[str] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.lock_path = Path(lock_path)
        self.base_dir = Path(base_dir)
        self.runs_dir = (
            Path(runs_dir) if runs_dir else self.base_dir.parent / f"{self.base_dir.name}.runs"
        )
        self.git_command = git_command or ["git"]
        self._cache_locks: dict[str, FileLock] = {}
        self._lockfile_lock_path = self.lock_path.with_suffix(".lock")
        self._lockfile_lock = FileLock(str(self._lockfile_lock_path))

    def get_skill_dir(self, skill_key: str, execution_id: str) -> Path:
        workflow, capability = self._parse_skill_key(skill_key)
        cache_dir = self._resolve_cache_dir(workflow, capability)
        source = self._source_for(skill_key)

        cache_lock = self._cache_lock_for(cache_dir)
        with cache_lock:
            self._ensure_cached(source.repo, skill_key, cache_dir)

        run_dir = self.runs_dir / execution_id / workflow / capability
        if run_dir.exists():
            shutil.rmtree(run_dir)
        shutil.copytree(cache_dir, run_dir, ignore=shutil.ignore_patterns(".git"))
        return run_dir

    def _parse_skill_key(self, skill_key: str) -> tuple[str, str]:
        if not skill_key:
            raise SkillPathError("skill key must not be empty")
        if skill_key.startswith("/"):
            raise SkillPathError(f"skill key must be relative: {skill_key!r}")
        parts = skill_key.split("/")
        if ".." in parts:
            raise SkillPathError(f"skill key must not contain '..': {skill_key!r}")
        if len(parts) != 2 or not all(parts):
            raise SkillPathError(f"skill key must be <workflow>/<capability>: {skill_key!r}")
        return parts[0], parts[1]

    def _resolve_cache_dir(self, workflow: str, capability: str) -> Path:
        candidate = (self.base_dir / workflow / capability).resolve()
        try:
            candidate.relative_to(self.base_dir.resolve())
        except ValueError as exc:
            raise SkillPathError(f"skill cache dir escapes base dir: {candidate}") from exc
        return candidate

    def _source_for(self, skill_key: str):
        config = self._load_config()
        source = config.skills.get(skill_key)
        if source is None:
            raise SkillConfigError(f"skill {skill_key!r} not declared in {self.config_path}")
        return source

    def _load_config(self) -> SkillsConfig:
        if not self.config_path.is_file():
            raise SkillConfigError(f"skills config not found: {self.config_path}")
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return SkillsConfig.model_validate(data)

    def _ensure_cached(self, repo: str, skill_key: str, cache_dir: Path) -> None:
        if not cache_dir.exists():
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(["clone", repo, str(cache_dir)])
        elif not (cache_dir / ".git").is_dir():
            raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

        lock = self._load_lock()
        locked = lock.skills.get(skill_key)
        if locked is not None and locked.commit:
            commit = locked.commit
            if not self._has_commit(cache_dir, commit):
                self._run_git(["-C", str(cache_dir), "fetch", "origin", commit])
        else:
            self._run_git(["-C", str(cache_dir), "fetch", "origin"])
            commit = self._rev_parse(cache_dir, "FETCH_HEAD")
            locked = LockedSkillSource(
                repo=repo, ref=self._source_for(skill_key).ref, commit=commit
            )
            lock.skills[skill_key] = locked
            self._atomic_write_lock(lock)

        self._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
        self._run_git(["-C", str(cache_dir), "clean", "-fd"])

    def _has_commit(self, cache_dir: Path, commit: str) -> bool:
        result = subprocess.run(
            self.git_command + ["-C", str(cache_dir), "cat-file", "-t", commit],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "commit" in result.stdout

    def _rev_parse(self, cache_dir: Path, rev: str) -> str:
        result = subprocess.run(
            self.git_command + ["-C", str(cache_dir), "rev-parse", rev],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _run_git(self, args: list[str]) -> None:
        cmd = self.git_command + args
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise SkillRepoError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")

    def _load_lock(self) -> SkillsLock:
        if not self.lock_path.is_file():
            return SkillsLock()
        data = yaml.safe_load(self.lock_path.read_text(encoding="utf-8")) or {}
        return SkillsLock.model_validate(data)

    def _atomic_write_lock(self, lock: SkillsLock) -> None:
        lock.resolved_at = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lockfile_lock:
            payload = yaml.safe_dump(lock.model_dump(), sort_keys=False)
            tmp_path = self.lock_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self.lock_path)

    def _cache_lock_for(self, cache_dir: Path) -> FileLock:
        key = str(cache_dir.resolve())
        if key not in self._cache_locks:
            lock_path = cache_dir.with_suffix(".lock")
            self._cache_locks[key] = FileLock(str(lock_path))
        return self._cache_locks[key]
