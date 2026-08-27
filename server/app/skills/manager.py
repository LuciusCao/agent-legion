from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol

from filelock import FileLock

from server.app.skills.cache_state import cache_at_commit
from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock, SkillSourceConfig
from server.app.skills.doc_cache import SkillDocCache
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError
from server.app.skills.paths import default_skills_runs_dir, ensure_secure_runs_dir
from server.app.skills.runs_gc import (
    DEFAULT_MAX_AGE_SECONDS,
    sweep_stale_execution_dirs,
)

logger = logging.getLogger(__name__)

_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SkillStore(Protocol):
    """Persistence contract for the skill source documents (DB or in-memory).

    None means the document was never seeded; the manager treats it as an
    empty document (same semantics as the retired missing-file behavior).
    """

    def get_sources(self) -> SkillsConfig | None: ...

    def get_lock(self) -> SkillsLock | None: ...

    def put_lock(self, lock: SkillsLock) -> None: ...


class SkillManager:
    def __init__(
        self,
        store: SkillStore,
        base_dir: Path,
        runs_dir: Path | None = None,
        git_command: list[str] | None = None,
        doc_cache_ttl_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self.base_dir = Path(base_dir)
        self.runs_dir = Path(runs_dir) if runs_dir else default_skills_runs_dir()
        self.git_command = git_command or ["git"]
        self._cache_locks: dict[str, FileLock] = {}
        # Serializes the read-modify-write of the DB lock document within this
        # process; cross-process git concurrency stays on the runs_dir cache
        # locks (filesystem-level, see _cache_lock_for).
        self._lock_write_lock = threading.Lock()
        # Memoizes the DB-backed source/lock documents on hot paths; see
        # doc_cache.py for the staleness semantics.
        self._doc_cache = SkillDocCache(doc_cache_ttl_seconds)
        # (cache_dir, commit) pairs verified present in the local repo. Git
        # object stores are append-only under this manager (clone/fetch add
        # objects, nothing prunes), so only positive results are memoized.
        self._known_commits: set[tuple[str, str]] = set()
        # cache_dir -> (commit, monotonic time) of the last successful
        # cleanliness probe (rev-parse + status). The probes are re-run once
        # the entry ages past the doc-cache TTL: relocks already become
        # visible within that TTL, and an outside dirtying is caught on the
        # first probe after expiry — the same staleness class.
        self._verified_clean: dict[str, tuple[str, float]] = {}
        self._repo_state_ttl = doc_cache_ttl_seconds

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
            # Secure-root first use: never mkdir into a pre-created or
            # symlinked runs dir on a shared temp filesystem.
            ensure_secure_runs_dir(self.runs_dir)
            shutil.copytree(cache_dir, run_dir, ignore=shutil.ignore_patterns(".git"))
        return run_dir

    def cleanup_execution(self, execution_id: str) -> None:
        self._validate_execution_id(execution_id)
        execution_dir = self._resolve_execution_dir(execution_id)
        if execution_dir.exists():
            shutil.rmtree(execution_dir)

    def sweep_stale_executions(self, *, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> int:
        """Leak GC over the runs dir; see ``runs_gc.sweep_stale_execution_dirs``."""
        return sweep_stale_execution_dirs(self.runs_dir, max_age_seconds=max_age_seconds)

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
            raise SkillConfigError(f"skill {skill_key!r} not declared in the DB skill sources")
        return source

    def _load_config(self) -> SkillsConfig:
        return self._doc_cache.read("sources", self._store.get_sources) or SkillsConfig()

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
            # A fresh clone may lack commits the old repo held (e.g. locked
            # commits detached from any ref), so drop the memoized presence
            # checks for this cache dir.
            self._known_commits = {k for k in self._known_commits if k[0] != str(cache_dir)}
            self._verified_clean.pop(str(cache_dir), None)
        elif not (cache_dir / ".git").is_dir():
            raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

        with self._lock_write_lock:
            lock = self._load_lock()
            locked = lock.skills.get(skill_key)

        if locked is not None and locked.commit:
            if locked.repo != source.repo or locked.ref != source.ref:
                raise SkillConfigError(
                    f"skill {skill_key!r} config differs from the published skill lock; "
                    "refresh the lock"
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
            with self._lock_write_lock:
                # Read-modify-write must build on a fresh read, not the cached
                # doc: a stale base could drop entries written by the admin
                # relock flow (which runs through other instances/processes).
                lock = self._store.get_lock() or SkillsLock()
                current = lock.skills.get(skill_key)
                if current is None:
                    lock.skills[skill_key] = LockedSkillSource(
                        repo=source.repo,
                        ref=source.ref,
                        commit=commit,
                    )
                    self._write_lock_unlocked(lock)

        # Read-only fast path (issue #42). The rev-parse/status probes are
        # memoized per cache dir for the doc-cache TTL (see __init__); a
        # commit change always misses the memo and re-probes. Within the TTL
        # window a relock+checkout by another process is invisible here, so
        # the cache content may briefly diverge from the commit this process
        # believes is checked out; the expiry re-probe self-heals.
        verified = self._verified_clean.get(str(cache_dir), ("", 0.0))
        now = time.monotonic()
        if verified[0] != commit or now - verified[1] >= self._repo_state_ttl:
            if not cache_at_commit(self._run_git, cache_dir, commit):
                self._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
                self._run_git(["-C", str(cache_dir), "clean", "-fd"])
            self._verified_clean[str(cache_dir)] = (commit, now)

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
        key = (str(cache_dir), commit)
        if key in self._known_commits:
            return True
        result = self._run_git(["-C", str(cache_dir), "cat-file", "-t", commit], check=False)
        found = result.returncode == 0 and "commit" in result.stdout
        if found:
            self._known_commits.add(key)
        return found

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
        """Return the current skill lock (empty when the document is missing)."""
        return self._load_lock()

    def _load_lock(self) -> SkillsLock:
        return self._doc_cache.read("lock", self._store.get_lock) or SkillsLock()

    def _write_lock_unlocked(self, lock: SkillsLock) -> None:
        """Persist ``lock`` through the store.

        The caller must already hold ``self._lock_write_lock``.
        """
        lock.resolved_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self._store.put_lock(lock)
        self._doc_cache.store("lock", lock)

    def _cache_lock_for(self, cache_dir: Path) -> FileLock:
        key = str(cache_dir.resolve())
        if key not in self._cache_locks:
            # Skills base dir is a read-only input (issue #42): locks live under runs_dir.
            ensure_secure_runs_dir(self.runs_dir)
            lock_dir = self.runs_dir / ".locks"
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            name = f"{cache_dir.parent.name}--{cache_dir.name}.lock"
            self._cache_locks[key] = FileLock(str(lock_dir / name))
        return self._cache_locks[key]
