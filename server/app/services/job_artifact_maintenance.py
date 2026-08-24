"""Job artifact maintenance: reconciler re-upload + job_dir cache eviction.

Two slow-cadence duties for the D12 artifact object store
(EXEC-ARTIFACT-STORE-001), driven by one thread mirroring the orphan-GC loop
discipline:

- ``reupload_missing`` — the completion hooks upload best-effort and never
  fail a node on a storage outage; this pass finds declared outputs of
  recently completed nodes that have no ``job_artifacts`` row yet and
  uploads them from the local job_dir copy.
- ``evict_cache_to_capacity`` — the local job_dir is an evictable cache
  under ``AGENT_LEGION_JOB_CACHE_MAX_BYTES``. Only files with a confirmed
  manifest row (i.e. durably stored) are ever unlinked, only for
  ``completed`` jobs without an active lease (running/failed jobs may still
  schedule or re-run nodes against the local copies), oldest mtime first.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from server.app.db.transaction import read_connection
from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir

logger = logging.getLogger(__name__)

JOB_CACHE_MAX_BYTES_ENV = "AGENT_LEGION_JOB_CACHE_MAX_BYTES"
DEFAULT_JOB_CACHE_MAX_BYTES = 50 * 1024**3
_REUPLOAD_WINDOW_DAYS = 7


def job_cache_max_bytes(env: Any = None) -> int:
    """Byte budget for the local job_dir cache (env-only, 50 GiB default)."""
    environ = os.environ if env is None else env
    raw = environ.get(JOB_CACHE_MAX_BYTES_ENV, "")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_JOB_CACHE_MAX_BYTES


def reupload_missing(
    store: JobArtifactObjectStore,
    job_db: JobQueries,
    settings: Settings,
    *,
    window_days: int = _REUPLOAD_WINDOW_DAYS,
) -> int:
    """Upload declared outputs of recently completed nodes that lack a row.

    Returns the number of artifacts (re)uploaded. Per-file failures are
    logged and skipped — the next pass retries.
    """
    if not store.enabled:
        return 0
    with read_connection(job_db.path) as conn:
        rows = conn.execute(
            "select distinct job_id, node_key from node_runs"
            " where status='completed'"
            " and finished_at > now() - make_interval(days => %s)",
            (window_days,),
        ).fetchall()
    completed_nodes: dict[str, set[str]] = {}
    for row in rows:
        completed_nodes.setdefault(str(row["job_id"]), set()).add(str(row["node_key"]))
    uploaded = 0
    for job_id, node_keys in completed_nodes.items():
        job = job_db.get_job(job_id)
        if job is None:
            continue
        try:
            definition = definition_from_job_snapshot(job) or require_workspace_active_definition(
                job_db, str(job["workspace_id"]), str(job["workflow_key"])
            )
            job_dir = resolve_job_dir(job, settings.jobs_dir)
        except Exception:  # skip jobs whose definition/dir cannot be resolved
            continue
        if not job_dir.is_dir():
            continue
        for node_key in node_keys:
            node = definition.nodes.get(node_key)
            if node is None:
                continue
            for name in node.outputs:
                if store.row_for_node(job_id, node_key, name) is not None:
                    continue
                local_path = job_dir / name
                if not local_path.is_file():
                    continue
                try:
                    store.upload(
                        workspace_id=str(job["workspace_id"]),
                        job_id=job_id,
                        node_key=node_key,
                        name=name,
                        local_path=local_path,
                    )
                    uploaded += 1
                except Exception:
                    logger.warning(
                        "reconciler re-upload failed for job %s node %s artifact %s",
                        job_id,
                        node_key,
                        name,
                        exc_info=True,
                    )
    return uploaded


def _job_dir_bytes(jobs_dir: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(jobs_dir):
        dirs[:] = [d for d in dirs if d != ".trash"]
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


def _eviction_candidates(
    store: JobArtifactObjectStore,
    job_db: JobQueries,
    settings: Settings,
) -> list[tuple[float, int, Path]]:
    """(mtime, size, path) of confirmed-uploaded root files of completed jobs."""
    with read_connection(job_db.path) as conn:
        rows = conn.execute(
            "select id, workspace_id, storage_dir from jobs"
            " where status='completed'"
            " and not exists ("
            "   select 1 from executor_leases l"
            "   where l.job_id = jobs.id and l.status = 'active'"
            " )"
        ).fetchall()
    candidates: list[tuple[float, int, Path]] = []
    for row in rows:
        job = dict(row)
        job_id = str(job["id"])
        confirmed = store.names_for_job(job_id)
        if not confirmed:
            continue
        try:
            job_dir = resolve_job_dir(job, settings.jobs_dir)
        except ManagedPathError:
            continue
        if not job_dir.is_dir():
            continue
        for entry in job_dir.iterdir():
            if entry.name not in confirmed or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            candidates.append((stat.st_mtime, stat.st_size, entry))
    candidates.sort(key=lambda item: item[0])
    return candidates


def evict_cache_to_capacity(
    store: JobArtifactObjectStore,
    job_db: JobQueries,
    settings: Settings,
    *,
    max_bytes: int | None = None,
) -> int:
    """Evict confirmed-uploaded cache files (oldest first) down to budget.

    Returns the number of files unlinked. The invariant is one-directional:
    a file is only ever unlinked when its ``job_artifacts`` row exists, so
    the object store stays the durable copy and reads fall back to it.
    """
    if not store.enabled:
        return 0
    budget = max_bytes if max_bytes is not None else job_cache_max_bytes()
    total = _job_dir_bytes(settings.jobs_dir)
    if total <= budget:
        return 0
    evicted = 0
    for _mtime, size, path in _eviction_candidates(store, job_db, settings):
        if total <= budget:
            break
        try:
            path.unlink()
            total -= size
            evicted += 1
        except OSError:
            logger.warning("failed to evict artifact cache file %s", path, exc_info=True)
    return evicted


class JobArtifactMaintenanceThread:
    """Slow-cadence driver; mirrors ArtifactOrphanGcThread's loop discipline.

    The first run happens after one full interval: maintenance is
    low-urgency and a boot-time scan only competes with startup work.
    """

    def __init__(
        self,
        store: JobArtifactObjectStore,
        job_db: JobQueries,
        settings: Settings,
        *,
        interval_seconds: float = 3600.0,
    ) -> None:
        self._store = store
        self._job_db = job_db
        self._settings = settings
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="job-artifact-maintenance", daemon=True
        )
        self._thread.start()

    def run_once(self) -> None:
        if not self._store.enabled:
            return
        uploaded = reupload_missing(self._store, self._job_db, self._settings)
        if uploaded:
            logger.info("job artifact reconciler uploaded %d artifacts", uploaded)
        evicted = evict_cache_to_capacity(self._store, self._job_db, self._settings)
        if evicted:
            logger.info("job artifact cache GC evicted %d files", evicted)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                logger.exception("job artifact maintenance failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
