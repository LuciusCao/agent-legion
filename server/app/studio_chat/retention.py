"""Studio chat session retention (#1041): TTL purge of archived / deleted sessions.

Archive (#924, v90) and soft delete (#872, v89) are pure visibility stamps;
without retention the stamped rows and their full message history stay
forever. ``studio_chat_retention_days`` (instance settings document, admin
global settings page) bounds that: 0 = disabled — the default, nothing is
ever removed — and a positive value makes the slow sweep physically delete
sessions whose ``coalesce(deleted_at, archived_at)`` is older than the
window.

Safety (the conservative union of both guards, issue acceptance "活跃会话
永不受影响"):

- the SQL only ever matches ``status in ('closed', 'error')`` rows carrying
  an expired stamp, and the delete statement re-checks the same predicate, so
  an unarchive (stamp cleared) or a resume (status left closed) racing the
  page read wins and the row survives;
- the purge additionally skips any session that still has an in-process
  runtime (a snapshot taken under ``_runtimes_lock``). The lock is released
  before the DELETE: it guards every chat event path in this process, and a
  batch's cascading delete must not stall live conversations. Dropping it is
  safe because a *new* runtime cannot appear for a candidate in between —
  the resume claim only moves an unstamped closed/error row to ``starting``
  (and the spawn registration fence refuses stamped rows), so any session
  that could gain a runtime has already failed the DELETE's own predicate
  re-check.

Deletion order (AGENTS.md §6 multi-step discipline): the message store is the
``studio_chat_messages`` table, FK ``on delete cascade`` to the session row,
so one DELETE statement removes the row and its messages atomically — a crash
mid-sweep leaves either both or neither, never orphan messages or a dangling
row. Agent-side state outside Agent Legion (e.g. the ACP agent's own session
directory) is owned by that agent and is not touched here.

Audit: every pass that removes rows logs one INFO line with the count, the
window and the cutoff; a skipped-with-runtime row logs a WARNING.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from server.app.db.dialect import ConnectSource
from server.app.services.instance_settings_store import InstanceSettingsStore

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0


def studio_chat_retention_days(connect_source: ConnectSource) -> int:
    """Effective chat retention in days (0 = disabled); read fresh.

    Same contract as ``execution_retention.execution_retention_days``: read
    from the DB document on every use (sweep and the session list's
    countdown), so admin edits take effect without a restart. Anything but a
    non-negative int degrades to 0 (disabled).
    """
    stored = InstanceSettingsStore(connect_source).get()
    if stored is None:
        return 0
    value = stored.get("studio_chat_retention_days", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def sweep_expired_chat_sessions(service: StudioChatService, *, now: datetime | None = None) -> int:
    """Purge closed sessions archived / deleted longer than the window.

    Returns the number of sessions removed (0 when retention is disabled).
    """
    days = studio_chat_retention_days(service.db)
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    db = service.db
    purged = 0
    after_id = ""
    while True:
        ids = db.page_expired_studio_chat_sessions(cutoff, after_id, BATCH_SIZE)
        if not ids:
            break
        after_id = ids[-1]
        # Snapshot only under the lock; the DELETE runs after release (see
        # module docstring for why its predicate re-check makes that safe).
        with service._runtimes_lock:
            live = [sid for sid in ids if sid in service._runtimes]
        eligible = [sid for sid in ids if sid not in live]
        purged += len(db.purge_expired_studio_chat_sessions(eligible, cutoff))
        if live:
            logger.warning(
                "studio chat retention skipped %d expired session(s) with a live runtime: %s",
                len(live),
                ", ".join(live),
            )
        if len(ids) < BATCH_SIZE:
            break
    if purged:
        logger.info(
            "studio chat retention purged %d session(s) archived/deleted before %s"
            " (window %d day(s))",
            purged,
            cutoff.isoformat(),
            days,
        )
    return purged


class StudioChatRetentionThread:
    """Slow-cadence driver; mirrors ExecutionRetentionThread's discipline.

    Runs only on the replica that owns the sweeper role, like the other slow
    sweeps; the first run happens after one full interval.
    """

    def __init__(
        self,
        service: StudioChatService,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._service = service
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="studio-chat-retention-sweeper", daemon=True
        )
        self._thread.start()

    def run_once(self) -> None:
        sweep_expired_chat_sessions(self._service)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                # #204 broad-except audit: the sweeper thread's life support
                # (same discipline as ExecutionRetentionThread). A DB restart
                # or settings-read failure mid-pass must not kill the only
                # thread performing chat retention; the traceback is logged
                # and the next interval is the retry. Each batch is its own
                # atomic DELETE, so a failed pass loses nothing and the retry
                # simply re-pages the (smaller) expired tail.
                logger.exception("studio chat retention sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
