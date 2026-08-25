"""Materials TTL sweep (materials-and-runs design §10, #160).

Two duties for ``materials.expires_at`` (written at completion time from the
``materials_ttl_days`` instance setting), driven by one slow-cadence thread
mirroring ``JobArtifactMaintenanceThread``'s loop discipline:

- ``expire_due_materials`` — flip ``ready`` rows past ``expires_at`` to
  ``expired``. Referencing jobs are NOT invalidated (v1 blunt semantics,
  mirroring ``MaterialsService.delete``); new references are rejected
  because every resolution point (run creation, claim/runtime
  materialization) only accepts ``status='ready'``.
- ``collect_expired_materials`` — physically delete ``expired`` rows past a
  short grace window whose job-input reference count is zero (same
  ``input_json`` check as ``delete()``) and which no bundle manifest
  references (same ``material_bundle_members`` guard as ``delete()``,
  #156): the S3 object goes first, the row second, so an object-delete
  failure rolls back and retries next pass.

Each material is its own small transaction; a bucket lifecycle rule stays
the backstop for orphans (deployment doc).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0
DELETE_GRACE_SECONDS = 600


def materials_ttl_days(database_dsn: DatabaseDsn) -> int:
    """Effective materials TTL in days (0 = disabled); read fresh per call.

    Unlike the restart-hydrated instance scalars, the TTL is consumed at
    material completion/sweep time, so it is read from the DB document on
    every use — edits take effect without a restart. Defensive against
    out-of-band writes: anything but a positive int degrades to 0.
    """
    stored = InstanceSettingsStore(database_dsn).get()
    if stored is None:
        return 0
    value = stored.get("materials_ttl_days", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def mark_ready(conn: Any, database_dsn: DatabaseDsn, material_id: str) -> None:
    """Mark a verified material ready, stamping expires_at from the TTL.

    The TTL is read fresh from the instance document at every completion —
    0/absent disables expiry (``expires_at`` stays NULL).
    """
    ttl_days = materials_ttl_days(database_dsn)
    conn.execute(
        "update materials set status='ready',"
        " expires_at = case when %s > 0 then now() + make_interval(days => %s) end"
        " where id=%s",
        (ttl_days, ttl_days, material_id),
    )


def expire_due_materials(database_dsn: DatabaseDsn) -> int:
    """Flip ready rows past ``expires_at`` to expired; returns the count."""
    with read_connection(database_dsn) as conn:
        rows = conn.execute(
            "select id from materials where status='ready'"
            " and expires_at is not null and expires_at <= now()"
        ).fetchall()
    expired = 0
    for row in rows:
        with write_transaction(database_dsn) as conn:
            updated = conn.execute(
                "update materials set status='expired' where id=%s and status='ready'",
                (str(row["id"]),),
            ).rowcount
        expired += int(updated)
    return expired


def collect_expired_materials(
    database_dsn: DatabaseDsn,
    storage: ObjectStorage,
    *,
    grace_seconds: int = DELETE_GRACE_SECONDS,
) -> int:
    """Delete expired materials past grace with zero referencing jobs.

    Returns the number of rows removed. Per material the re-check (row still
    expired, still unreferenced) and the delete happen in one transaction;
    object deletion failures keep the row for the next pass (same ordering
    discipline as ``MaterialsService.delete``).
    """
    with read_connection(database_dsn) as conn:
        candidates = conn.execute(
            "select id, workspace_id from materials where status='expired'"
            " and expires_at is not null"
            " and expires_at <= now() - make_interval(secs => %s)",
            (grace_seconds,),
        ).fetchall()
    deleted = 0
    for candidate in candidates:
        material_id = str(candidate["id"])
        try:
            with write_transaction(database_dsn) as conn:
                row = conn.execute(
                    "select * from materials where id=%s and status='expired' for update",
                    (material_id,),
                ).fetchone()
                if row is None:
                    continue
                referencing = conn.execute(
                    "select id from jobs where workspace_id=%s"
                    " and input_json::jsonb ->> 'type' = 'material'"
                    " and input_json::jsonb ->> 'material_id' = %s limit 1",
                    (str(candidate["workspace_id"]), material_id),
                ).fetchone()
                if referencing is not None:
                    continue
                # Bundle 成员同样算引用（#156）：与 MaterialsService.delete
                # 同一守卫——必须先删 bundle 清单，否则对象已删而行删除被
                # 外键回滚，仍存在的 bundle 永远无法物化。
                member_of = conn.execute(
                    "select bundle_id from material_bundle_members where material_id=%s limit 1",
                    (material_id,),
                ).fetchone()
                if member_of is not None:
                    continue
                storage.delete_object(str(row["storage_key"]))
                conn.execute("delete from materials where id=%s", (material_id,))
            deleted += 1
        except Exception:
            logger.warning(
                "failed to collect expired material %s; retrying next pass",
                material_id,
                exc_info=True,
            )
    return deleted


class MaterialTtlSweeperThread:
    """Slow-cadence driver; mirrors JobArtifactMaintenanceThread's discipline.

    The first run happens after one full interval: the sweep is low-urgency
    and a boot-time scan only competes with startup work.
    """

    def __init__(
        self,
        database_dsn: DatabaseDsn,
        storage: ObjectStorage | None,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._dsn = database_dsn
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="material-ttl-sweeper", daemon=True)
        self._thread.start()

    def run_once(self) -> None:
        # No object storage means no materials feature: rows can only reach
        # ready/expired through the storage-backed upload flow.
        if self._storage is None:
            return
        expired = expire_due_materials(self._dsn)
        if expired:
            logger.info("materials TTL sweep expired %d material(s)", expired)
        deleted = collect_expired_materials(self._dsn, self._storage)
        if deleted:
            logger.info("materials TTL sweep collected %d material(s)", deleted)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                logger.exception("materials TTL sweep failed")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None
