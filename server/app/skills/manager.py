from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml
from filelock import FileLock

from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock, SkillSourceConfig
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError

logger = logging.getLogger(__name__)

_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
        self._lockfile_lock_path = self.lock_path.with_suffix(self.lock_path.suffix + ".lock")
        self._lockfile_lock = FileLock(str(self._lockfile_lock_path))

    def get_skill_dir(self, skill_key: str, execution_id: str) -> Path:
        self._validate_execution_id(execution_id)
        workflow, capability = self._parse_skill_key(skill_key)
        cache_dir = self._resolve_cache_dir(workflow, capability)
        run_dir = self._resolve_run_dir(execution_id, workflow, capability)
        source = self._source_for(skill_key)

        cache_lock = self._cache_lock_for(cache_dir)
        with cache_lock:
            self._ensure_cached(source, skill_key, cache_dir)

            if run_dir.exists():
                shutil.rmtree(run_dir)
            shutil.copytree(cache_dir, run_dir, ignore=shutil.ignore_patterns(".git"))
        return run_dir

    def cleanup_execution(self, execution_id: str) -> None:
        self._validate_execution_id(execution_id)
        execution_dir = self._resolve_execution_dir(execution_id)
        if execution_dir.exists():
            shutil.rmtree(execution_dir)

    def _validate_execution_id(self, execution_id: str) -> None:
        if not execution_id:
            raise SkillPathError("execution_id must not be empty")
        if os.path.isabs(execution_id):
            raise SkillPathError(f"execution_id must not be an absolute path: {execution_id!r}")
        if "/" in execution_id or "\\" in execution_id:
            raise SkillPathError(f"execution_id must not contain path separators: {execution_id!r}")
        if ".." in execution_id:
            raise SkillPathError(f"execution_id must not contain '..': {execution_id!r}")
        if not _EXECUTION_ID_RE.match(execution_id):
            raise SkillPathError(f"execution_id contains unsafe characters: {execution_id!r}")

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

    def _resolve_run_dir(self, execution_id: str, workflow: str, capability: str) -> Path:
        execution_dir = self._resolve_execution_dir(execution_id)
        root = self.runs_dir.resolve()
        candidate = (execution_dir / workflow / capability).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SkillPathError(f"skill run dir escapes runs dir: {candidate}") from exc
        return candidate

    def _resolve_execution_dir(self, execution_id: str) -> Path:
        root = self.runs_dir.resolve()
        candidate = (root / execution_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SkillPathError(f"skill execution dir escapes runs dir: {candidate}") from exc
        return candidate

    def _source_for(self, skill_key: str) -> SkillSourceConfig:
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

    def _ensure_cached(
        self,
        source: SkillSourceConfig,
        skill_key: str,
        cache_dir: Path,
    ) -> None:
        repo = self._normalize_repo(source.repo)
        in_place = self._is_in_place_source(repo, cache_dir)
        if not cache_dir.exists():
            if in_place:
                raise SkillRepoError(f"local skill repo not found: {cache_dir}")
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(["clone", repo, str(cache_dir)])
        elif not (cache_dir / ".git").is_dir():
            raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

        with self._lockfile_lock:
            lock = self._load_lock()
            locked = lock.skills.get(skill_key)

        if locked is not None and locked.commit:
            if locked.repo != source.repo or locked.ref != source.ref:
                raise SkillConfigError(
                    f"skill {skill_key!r} config differs from skills.lock; refresh the lock"
                )
            commit = locked.commit
            if not self._has_commit(cache_dir, commit):
                if in_place:
                    raise SkillRepoError(
                        f"locked commit {commit!r} is missing from local skill repo {cache_dir}"
                    )
                self._run_git(["-C", str(cache_dir), "fetch", "origin", commit])
                fetched_commit = self._rev_parse(cache_dir, "FETCH_HEAD")
                commit = fetched_commit
        else:
            commit = self._resolve_source_ref(cache_dir, source.ref, in_place=in_place)
            with self._lockfile_lock:
                lock = self._load_lock()
                current = lock.skills.get(skill_key)
                if current is None:
                    lock.skills[skill_key] = LockedSkillSource(
                        repo=source.repo,
                        ref=source.ref,
                        commit=commit,
                    )
                    self._write_lock_unlocked(lock)

        self._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
        self._run_git(["-C", str(cache_dir), "clean", "-fd"])

    def _refresh_source(
        self,
        skill_key: str,
        source: SkillSourceConfig,
        cache_dir: Path,
    ) -> LockedSkillSource:
        repo = self._normalize_repo(source.repo)
        in_place = self._is_in_place_source(repo, cache_dir)
        if not cache_dir.exists():
            if in_place:
                raise SkillRepoError(f"local skill repo not found: {cache_dir}")
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(["clone", repo, str(cache_dir)])
        elif not (cache_dir / ".git").is_dir():
            raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

        commit = self._resolve_source_ref(cache_dir, source.ref, in_place=in_place)
        self._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
        self._run_git(["-C", str(cache_dir), "clean", "-fd"])
        logger.info("Refreshed Pi skill %s to %s", skill_key, commit)
        return LockedSkillSource(repo=source.repo, ref=source.ref, commit=commit)

    def _resolve_source_ref(self, cache_dir: Path, ref: str, *, in_place: bool) -> str:
        if in_place:
            return self._rev_parse(cache_dir, ref)
        self._run_git(["-C", str(cache_dir), "fetch", "origin", ref])
        return self._rev_parse(cache_dir, "FETCH_HEAD")

    def _normalize_repo(self, repo: str) -> str:
        if repo.startswith("~/") or Path(repo).is_absolute():
            return str(Path(repo).expanduser().resolve())
        return repo

    def _is_in_place_source(self, repo: str, cache_dir: Path) -> bool:
        if not Path(repo).is_absolute():
            return False
        return Path(repo).resolve() == cache_dir.resolve()

    def _has_commit(self, cache_dir: Path, commit: str) -> bool:
        result = self._run_git(["-C", str(cache_dir), "cat-file", "-t", commit], check=False)
        return result.returncode == 0 and "commit" in result.stdout

    def _rev_parse(self, cache_dir: Path, rev: str) -> str:
        # Use ^{commit} to resolve annotated tags to their underlying commit.
        result = self._run_git(["-C", str(cache_dir), "rev-parse", f"{rev}^{{commit}}"])
        return result.stdout.strip()

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = self.git_command + args
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace"
        )
        if check and result.returncode != 0:
            raise SkillRepoError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def load_lock(self) -> SkillsLock:
        """Return the current skills.lock contents (empty when the file is missing)."""
        return self._load_lock()

    def _load_lock(self) -> SkillsLock:
        if not self.lock_path.is_file():
            return SkillsLock()
        data = yaml.safe_load(self.lock_path.read_text(encoding="utf-8")) or {}
        return SkillsLock.model_validate(data)

    def _write_lock_unlocked(self, lock: SkillsLock) -> None:
        """Write ``lock`` to disk atomically.

        The caller must already hold ``self._lockfile_lock``.
        """
        lock.resolved_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        payload = yaml.safe_dump(lock.model_dump(), sort_keys=False)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.lock_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.lock_path)

    def _cache_lock_for(self, cache_dir: Path) -> FileLock:
        key = str(cache_dir.resolve())
        if key not in self._cache_locks:
            lock_path = cache_dir.with_suffix(".lock")
            self._cache_locks[key] = FileLock(str(lock_path))
        return self._cache_locks[key]
