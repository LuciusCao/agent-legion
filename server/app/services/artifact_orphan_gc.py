"""Orphan scan and reclamation for the content-addressed artifact store.

Job-deletion GC (``job_artifact_gc``) only covers hashes once referenced by
the deleted job. Blobs whose upload committed but whose follow-up ref was
never written (lost result report, crashed Worker, pre-claim job cleanup)
stay invisible to that path and accumulate forever. This module scans the
whole catalog for zero-reference rows — keyset-paginated by hash so a long
scan neither re-reads nor skips rows under concurrent puts/deletes — and
reclaims them through ``ArtifactStore.delete_unreferenced``, which
transactionally re-checks refcounts and the in-flight grace window before
unlinking any blob.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime

from server.app.db.transaction import read_connection
from server.app.services.artifact_store import ArtifactStore, _as_utc

logger = logging.getLogger(__name__)

_SCAN_BATCH = 500


def _orphan_batches(store: ArtifactStore) -> Iterator[list[dict]]:
    after = ""
    while True:
        with read_connection(store.db_path) as conn:
            rows = conn.execute(
                "select a.hash, a.size, a.created_at from artifacts a"
                " where a.hash > %s"
                " and not exists (select 1 from artifact_refs r where r.hash = a.hash)"
                " order by a.hash limit %s",
                (after, _SCAN_BATCH),
            ).fetchall()
        if not rows:
            return
        yield [dict(row) for row in rows]
        after = str(rows[-1]["hash"])


def _past_grace(store: ArtifactStore, row: dict, now: datetime) -> bool:
    created = _as_utc(row["created_at"])
    # Unknown timestamps are kept (delete_unreferenced makes the same call):
    # a blob whose age cannot be proven is never reclaimed.
    return created is not None and (now - created).total_seconds() > store.gc_grace_seconds


def orphan_stats(store: ArtifactStore, *, now: datetime | None = None) -> tuple[int, int]:
    """(count, total_bytes) of reclaimable orphans; the dry-run view."""
    now = now or datetime.now(UTC)
    count = 0
    total_bytes = 0
    for batch in _orphan_batches(store):
        for row in batch:
            if _past_grace(store, row, now):
                count += 1
                total_bytes += int(row["size"])
    return count, total_bytes


def gc_orphans(store: ArtifactStore) -> int:
    """Reclaim every zero-reference blob past grace; returns the reclaimed count."""
    reclaimed = 0
    for batch in _orphan_batches(store):
        reclaimed += store.delete_unreferenced([str(row["hash"]) for row in batch])
    return reclaimed


class ArtifactOrphanGcThread:
    """Slow-cadence orphan GC driver; mirrors SweeperThread's loop discipline.

    The first run happens after one full interval: orphans are low-urgency
    and a boot-time scan only competes with startup work.
    """

    def __init__(self, store: ArtifactStore, *, interval_seconds: float = 3600.0) -> None:
        self._store = store
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="artifact-orphan-gc", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                reclaimed = gc_orphans(self._store)
            except Exception:
                logger.exception("artifact orphan GC failed")
            else:
                if reclaimed:
                    logger.info("artifact orphan GC reclaimed %d blobs", reclaimed)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
