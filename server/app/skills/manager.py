from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Protocol

from filelock import FileLock

from server.app.skills.cache_state import cache_at_commit
from server.app.skills.config import LATEST_REF, LockedSkill, SkillsLock
from server.app.skills.doc_cache import SkillDocCache
from server.app.skills.errors import SkillPathError, SkillRepoError
from server.app.skills.paths import default_skills_runs_dir, ensure_secure_runs_dir
from server.app.skills.runs_gc import (
    DEFAULT_MAX_AGE_SECONDS,
    sweep_stale_execution_dirs,
)

logger = logging.getLogger(__name__)

_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SkillStore(Protocol):
    """Persistence contract for the skill lock document (DB or in-memory).

    None means the document was never seeded; the manager treats it as an
    empty document (same semantics as the retired missing-file behavior).
    """

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
        # object stores are append-only in these in-place repos (nothing
        # prunes), so only positive results are memoized.
        self._known_commits: set[tuple[str, str]] = set()

    def get_skill_dir(self, skill_key: str, execution_id: str, ref: str | None = None) -> Path:
        return self.checkout_skill(skill_key, execution_id, ref=ref)[0]

    def checkout_skill(
        self, skill_key: str, execution_id: str, ref: str | None = None
    ) -> tuple[Path, str, str]:
        # ``ref`` empty or "latest" follows the repo's live HEAD (never
        # locked); any other ref resolves through the lock. Returns (run_dir,
        # commit, version) where version is "ref@commit12" — formatted from
        # the pinned (ref, commit) pair, not probed from the shared cache
        # checkout, which oscillates between refs under multi-ref pinning.
        self._validate_execution_id(execution_id)
        workflow, capability = self._parse_skill_key(skill_key)
        cache_dir = self._resolve_cache_dir(workflow, capability)
        run_dir = self._resolve_run_dir(execution_id, workflow, capability)
        effective_ref = ref or LATEST_REF

        cache_lock = self._cache_lock_for(cache_dir)
        with cache_lock:
            if effective_ref == LATEST_REF:
                commit = self._ensure_latest(skill_key, cache_dir)
            else:
                commit = self._ensure_pinned(skill_key, cache_dir, effective_ref)

            if run_dir.exists():
                shutil.rmtree(run_dir)
            # Secure-root first use: never mkdir into a pre-created or
            # symlinked runs dir on a shared temp filesystem.
            ensure_secure_runs_dir(self.runs_dir)
            shutil.copytree(cache_dir, run_dir, ignore=shutil.ignore_patterns(".git"))
        return run_dir, commit, f"{effective_ref}@{commit[:12]}"

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

    def _require_cache_dir(self, skill_key: str, cache_dir: Path) -> None:
        """Fail with guidance when the in-place skill repo is missing/invalid.

        In-place is the only mode since #322: a skill's repo is the directory
        ``<skills root>/<group>/<name>`` itself; there is no clone/fetch
        channel. To use a skill from an external repository, clone it into
        the skills root manually — it then is a plain local repo.
        """
        if not cache_dir.exists():
            raise SkillRepoError(
                f"local skill repo not found: {cache_dir} — skill {skill_key!r} expects an "
                "in-place git repository at <skills root>/<group>/<name>; create or clone "
                "it there (示例 workflow 的 skill 请先运行 make import-demo 导入)"
            )
        if not (cache_dir / ".git").is_dir():
            raise SkillRepoError(f"cache dir exists but is not a git repo: {cache_dir}")

    def _ensure_latest(self, skill_key: str, cache_dir: Path) -> str:
        """Live-HEAD resolution for ``latest`` (and normalized empty) refs.

        The lock is deliberately neither read nor written: ``latest`` means
        "follow the repo's current HEAD", so every dispatch rev-parses it
        afresh (issue #322).
        """
        self._require_cache_dir(skill_key, cache_dir)
        commit = self._rev_parse(cache_dir, "HEAD")
        self._checkout_commit(cache_dir, commit)
        return commit

    def _ensure_pinned(self, skill_key: str, cache_dir: Path, ref: str) -> str:
        """Lock-backed resolution for an explicitly pinned ref (tag)."""
        self._require_cache_dir(skill_key, cache_dir)
        with self._lock_write_lock:
            lock = self._load_lock()
            locked = lock.skills.get(skill_key)
        commit = locked.refs.get(ref) if locked is not None else None
        if commit:
            if not self._has_commit(cache_dir, commit):
                raise SkillRepoError(
                    f"locked commit {commit!r} is missing from local skill repo {cache_dir}"
                )
        else:
            commit = self._rev_parse(cache_dir, ref)
            with self._lock_write_lock:
                # Read-modify-write must build on a fresh read, not the cached
                # doc: a stale base could drop entries written by the relock
                # CLI (which runs through other instances/processes).
                lock = self._store.get_lock() or SkillsLock()
                current = lock.skills.get(skill_key)
                if current is None:
                    # repo is audit-only since #322 (the location derives from
                    # skill_roots + key; nothing validates it).
                    current = LockedSkill(repo=str(cache_dir))
                    lock.skills[skill_key] = current
                if ref not in current.refs:
                    current.refs[ref] = commit
                    self._write_lock_unlocked(lock)
        self._checkout_commit(cache_dir, commit)
        return commit

    def _checkout_commit(self, cache_dir: Path, commit: str) -> None:
        # Always re-probe under the cache lock (correctness over the old
        # per-instance memo, retired after codex P1 on PR 317): a memoized
        # (commit, t) entry could skip this probe while another manager
        # instance had already switched the shared cache to a different ref —
        # the run dir would then silently get the other ref's content while
        # being recorded as this ref@commit. Two git probes per dispatch are
        # the accepted cost; cache_at_commit short-circuits on a clean match.
        if not cache_at_commit(self._run_git, cache_dir, commit):
            self._run_git(["-C", str(cache_dir), "checkout", commit, "-f"])
            self._run_git(["-C", str(cache_dir), "clean", "-fd"])

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
            # Defense in depth: exist_ok would let a symlinked .locks (inside
            # a root an operator pinned to an already-polluted path) redirect
            # the FileLocks; reject anything not owned-by-us real dir, and
            # normalize a stale mode (older-version creation).
            try:
                lock_dir.mkdir(mode=0o700)
            except FileExistsError:
                if os.path.islink(lock_dir) or not os.path.isdir(lock_dir):
                    raise OSError(
                        f"refusing to use skills lock dir {lock_dir}: it exists "
                        "but is not a directory (possible redirected lock "
                        "path). Remove it or pin AGENT_LEGION_SKILLS_RUNS_DIR."
                    ) from None
                os.chmod(lock_dir, 0o700)
            name = f"{cache_dir.parent.name}--{cache_dir.name}.lock"
            self._cache_locks[key] = FileLock(str(lock_dir / name))
        return self._cache_locks[key]
