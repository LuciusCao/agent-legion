"""Job artifact maintenance: reconciler re-upload + job_dir cache eviction.

Two slow-cadence duties for the D12 artifact object store
(EXEC-ARTIFACT-STORE-001), driven by one thread mirroring the orphan-GC loop
discipline:

- ``reupload_missing`` — the completion hooks upload best-effort and never
  fail a node on a storage outage; this pass finds declared outputs of
  recently completed nodes whose ``job_artifacts`` row is missing OR stale
  (a node re-run produced new bytes while the upload failed) and
  (re-)uploads them from the local job_dir copy.
- ``evict_cache_to_capacity`` — the local job_dir is an evictable cache
  under ``AGENT_LEGION_JOB_CACHE_MAX_BYTES``. Only files with a confirmed
  manifest row (i.e. durably stored, recorded size and content hash still
  matching the local file) are ever unlinked, only for ``completed`` jobs
  without an active lease (re-checked per file right before each unlink:
  running/failed jobs may still schedule or re-run nodes against the local
  copies), oldest mtime first.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_errors import NotFoundError
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir

#: Per-job expected failures (#204): #243-family corrupt revisions (incl.
#: non-mapping JSON), no active revision, unmappable/OS-failing storage_dir.
_EXPECTED_JOB_FAILURES = (
    NotFoundError,
    ManagedPathError,
    ValueError,  # #243 family: JSONDecodeError + WorkflowDefinitionError
    OSError,
    RuntimeError,
)

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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _row_stale(row: dict[str, Any], local_path: Path) -> bool:
    """True when the local file no longer matches the manifest row.

    A node re-run can produce new bytes while the best-effort upload fails;
    the old row must not suppress the re-upload forever (and must never
    certify the new local file for cache eviction).
    """
    try:
        if int(row["size_bytes"]) != local_path.stat().st_size:
            return True
    except OSError:
        return False
    recorded = str(row.get("content_hash") or "")
    return bool(recorded) and recorded != _file_sha256(local_path)


def reupload_missing(
    store: JobArtifactObjectStore,
    job_db: JobQueries,
    settings: Settings,
    *,
    window_days: int = _REUPLOAD_WINDOW_DAYS,
) -> int:
    """Upload declared outputs of recently completed nodes with no/stale row.

    Returns the number of artifacts (re)uploaded. Per-file failures are
    logged and skipped — the next pass retries.
    """
    if not store.enabled:
        return 0
    with job_db.read() as conn:
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
        except _EXPECTED_JOB_FAILURES as exc:
            # #204: expected per-job failures only — corrupt revision JSON
            # (JSONDecodeError/WorkflowDefinitionError, the #243 family incl.
            # non-mapping top level), no active revision, unmappable
            # storage_dir, or OS-level resolve failures (permissions/symlink
            # loops, codex review on PR #251). One bad job must not abort the
            # reconciler pass (and thereby eviction) for every other job;
            # genuine programming errors propagate to the thread's safety net.
            logger.debug("reconciler skips job %s: %s", job_id, exc)
            continue
        if not job_dir.is_dir():
            continue
        for node_key in node_keys:
            node = definition.nodes.get(node_key)
            if node is None:
                continue
            for name in node.outputs:
                local_path = job_dir / name
                if not local_path.is_file():
                    continue
                manifest_row = store.row_for_node(job_id, node_key, name)
                if manifest_row is not None and not _row_stale(manifest_row, local_path):
                    continue
                # 行缺失或已过期（rerun 产出新字节而上传失败）：（重新）上传，
                # upsert 刷新清单行。
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
                    # #204 broad-except audit: per-file best-effort re-upload.
                    # The upload path's outcome space is genuinely mixed —
                    # declared storage outages (botocore ClientError after the
                    # bounded retries inside upload), DB errors from the
                    # manifest upsert, and unexpected programming errors must
                    # all leave this pass alive for the other artifacts: the
                    # reconciler IS the retry mechanism (next pass re-finds
                    # the still-missing/stale row). A narrow business family
                    # cannot enumerate the storage layer; the traceback is
                    # logged so the outage is diagnosable.
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
) -> list[tuple[float, int, str, Path]]:
    """(mtime, size, job_id, path) of confirmed-uploaded root files.

    Confirmed = a manifest row exists AND its recorded size still matches the
    local file AND — whenever the manifest rows carry a content hash — the
    local file's sha256 equals one of them. A stale row (re-run produced new
    bytes while the upload failed) must never certify the new local copy for
    eviction: a same-size re-run is invisible to a size-only check, and the
    reconciler's re-upload is itself best-effort, so eviction re-hashes
    instead of trusting the row. The hash cost is only paid here — i.e. when
    the cache is already over budget and eviction is actually needed.
    """
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, workspace_id, storage_dir from jobs"
            " where status='completed'"
            " and not exists ("
            "   select 1 from executor_leases l"
            "   where l.job_id = jobs.id and l.status = 'active'"
            " )"
        ).fetchall()
    candidates: list[tuple[float, int, str, Path]] = []
    for row in rows:
        job = dict(row)
        job_id = str(job["id"])
        confirmed: dict[str, list[tuple[int, str]]] = {}
        for manifest_row in store.rows_for_job(job_id):
            confirmed.setdefault(str(manifest_row["name"]), []).append(
                (int(manifest_row["size_bytes"]), str(manifest_row.get("content_hash") or ""))
            )
        if not confirmed:
            continue
        try:
            job_dir = resolve_job_dir(job, settings.jobs_dir)
        except ManagedPathError:
            continue
        if not job_dir.is_dir():
            continue
        for entry in job_dir.iterdir():
            manifest_entries = confirmed.get(entry.name)
            if manifest_entries is None or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_size not in {size for size, _hash in manifest_entries}:
                continue  # size 不符 = 行未确认这份本地字节，不淘汰
            hashes = {hash_ for _size, hash_ in manifest_entries if hash_}
            if hashes and _file_sha256(entry) not in hashes:
                continue  # 同长度 rerun 新字节：hash 不符不得认证淘汰
            candidates.append((stat.st_mtime, stat.st_size, job_id, entry))
    candidates.sort(key=lambda item: item[0])
    return candidates


def _job_still_evictable(job_db: JobQueries, job_id: str) -> bool:
    """Per-unlink re-check of the eviction precondition for one job.

    The candidate snapshot is taken once; between snapshot and unlink a user
    re-run puts the job back to queued and re-executes nodes against the
    local copies. Never evict bytes for a job that is no longer ``completed``
    or has acquired an active lease in the meantime.
    """
    with job_db.read() as conn:
        row = conn.execute(
            "select 1 from jobs"
            " where id=%s and status='completed'"
            " and not exists ("
            "   select 1 from executor_leases l"
            "   where l.job_id = jobs.id and l.status = 'active'"
            " )",
            (job_id,),
        ).fetchone()
    return row is not None


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
    for _mtime, size, job_id, path in _eviction_candidates(store, job_db, settings):
        if total <= budget:
            break
        # 快照到 unlink 之间 job 可能被 rerun 置回 queued 并重新执行：
        # 每个文件 unlink 前都重查前提（不缓存结论），不再成立就跳过——
        # 同 job 的其余文件可能已被 rerun 写成新产物。
        if not _job_still_evictable(job_db, job_id):
            continue
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
        # 顺序纪律：reupload 先行修复缺失/过期清单行（rerun 新字节刷新行），
        # eviction 再凭「行存在且 size 相符」淘汰本地缓存——反向会让旧行
        # 淘汰掉尚未上传的新文件。
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
                # #204 broad-except audit: the maintenance thread's life
                # support. run_once (reupload + eviction) is a long walk over
                # the jobs table, manifest rows, and the filesystem — any
                # unexpected failure (DB restart mid-walk, a narrow catch
                # above missing a case) must not kill the only thread that
                # performs either duty: the local cache would then grow
                # unbounded and missing manifest rows would never self-heal.
                # The full traceback is logged; the next interval is the
                # retry.
                logger.exception("job artifact maintenance failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
