"""Sqlite write/read paths for the RemoteExecutionBroker queue.

Each write function owns its connect-and-transact unit via
``server.app.db.transaction.write_transaction`` and is decorated with
``retried_on_sqlite_lock`` so lock contention retries the whole unit on a
fresh connection. Rows live in the ``remote_executions`` table (migration
v021); ``outcome_json`` holds ``dataclasses.asdict(RemoteOutcome)`` with the
``command`` tuple serialized as a JSON list.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.db.retry import retried_on_sqlite_lock
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_transactions import _sqlite_timestamp

_DONE_RETENTION = timedelta(hours=24)


@retried_on_sqlite_lock
def claim_next(
    db_path: Path, worker_id: str, capabilities: Collection[str], now: str
) -> dict[str, Any] | None:
    """Atomically claim the oldest queued row matching ``capabilities``.

    Registered workers are capped at their ``remote_workers.slots`` held
    claims; unregistered workers keep the legacy no-slots behavior.
    """
    placeholders = ",".join("?" * len(capabilities))
    with write_transaction(db_path) as conn:
        row = conn.execute(
            "select slots from remote_workers where worker_id = ?", (worker_id,)
        ).fetchone()
        if row is not None:
            held = conn.execute(
                "select count(*) as cnt from remote_executions"
                " where state = 'claimed' and worker_id = ?",
                (worker_id,),
            ).fetchone()["cnt"]
            if held >= row["slots"]:
                return None
        candidate = conn.execute(
            f"select execution_id from remote_executions"
            f" where state = 'queued' and capability in ({placeholders})"
            f" order by created_at limit 1",
            tuple(capabilities),
        ).fetchone()
        if candidate is None:
            return None
        cursor = conn.execute(
            "update remote_executions set state = 'claimed', worker_id = ?,"
            " last_heartbeat_at = ?, updated_at = ?"
            " where execution_id = ? and state = 'queued'",
            (worker_id, now, now, candidate["execution_id"]),
        )
        if cursor.rowcount == 0:
            return None  # Lost a cross-connection race; the caller polls again.
        entry = conn.execute(
            "select * from remote_executions where execution_id = ?",
            (candidate["execution_id"],),
        ).fetchone()
    return dict(entry)


@retried_on_sqlite_lock
def heartbeat_claim(db_path: Path, execution_id: str, worker_id: str, now: str) -> bool:
    with write_transaction(db_path) as conn:
        cursor = conn.execute(
            "update remote_executions set last_heartbeat_at = ?, updated_at = ?"
            " where execution_id = ? and state = 'claimed' and worker_id = ?",
            (now, now, execution_id, worker_id),
        )
        return cursor.rowcount > 0


@retried_on_sqlite_lock
def finish_execution(
    db_path: Path,
    execution_id: str,
    worker_id: str,
    outcome_dict: dict[str, Any],
    now: str,
    *,
    before_update: Callable[[], None] | None = None,
) -> bool:
    """Mark a claimed row done; ``before_update`` (archive rename) runs inside
    the transaction, so a failure there rolls the finish back. The retry unit
    spans the whole call, so ``before_update`` must be idempotent."""
    with write_transaction(db_path) as conn:
        row = conn.execute(
            "select worker_id from remote_executions where execution_id = ? and state = 'claimed'",
            (execution_id,),
        ).fetchone()
        if row is None or row["worker_id"] != worker_id:
            return False
        # The claim record is authoritative for provenance; the reported
        # worker_id is ignored.
        outcome_dict = {**outcome_dict, "worker_id": row["worker_id"]}
        if before_update is not None:
            before_update()
        cursor = conn.execute(
            "update remote_executions set state = 'done', outcome_json = ?, updated_at = ?"
            " where execution_id = ? and state = 'claimed' and worker_id = ?",
            (json.dumps(outcome_dict), now, execution_id, worker_id),
        )
        return cursor.rowcount > 0


@retried_on_sqlite_lock
def cancel_execution(
    db_path: Path, execution_id: str, outcome_dict: dict[str, Any], now: str
) -> bool:
    """Finish a queued or claimed row as cancelled; no-op for unknown/done ids."""
    with write_transaction(db_path) as conn:
        row = conn.execute(
            "select state, worker_id from remote_executions where execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None or row["state"] == "done":
            return False
        outcome_dict = {**outcome_dict, "worker_id": row["worker_id"] or ""}
        cursor = conn.execute(
            "update remote_executions set state = 'done', outcome_json = ?, updated_at = ?"
            " where execution_id = ? and state != 'done'",
            (json.dumps(outcome_dict), now, execution_id),
        )
        return cursor.rowcount > 0


def bundle_name_for_claim(db_path: Path, execution_id: str, worker_id: str) -> str | None:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select bundle_name from remote_executions"
            " where execution_id = ? and state = 'claimed' and worker_id = ?",
            (execution_id, worker_id),
        ).fetchone()
    return row["bundle_name"] if row is not None else None


@retried_on_sqlite_lock
def sweep(
    db_path: Path, *, now: datetime, claim_timeout: timedelta, requeue_limit: int
) -> list[str]:
    """Requeue stale claims, fail rows past the requeue limit, and delete done
    rows older than the retention window. Returns execution ids failed here."""
    now_ts = _sqlite_timestamp(now)
    cutoff = _sqlite_timestamp(now - claim_timeout)
    done_cutoff = _sqlite_timestamp(now - _DONE_RETENTION)
    with write_transaction(db_path) as conn:
        stale = conn.execute(
            "select execution_id, worker_id, requeue_count from remote_executions"
            " where state = 'claimed' and last_heartbeat_at is not null"
            " and last_heartbeat_at < ?",
            (cutoff,),
        ).fetchall()
        finished: list[str] = []
        for row in stale:
            requeue_count = row["requeue_count"] + 1
            if requeue_count > requeue_limit:
                outcome: dict[str, Any] = {
                    "status": "failed",
                    "exit_code": 1,
                    "error_message": (
                        f"remote execution lost its worker {requeue_count} times;"
                        " requeue limit exceeded"
                    ),
                    "command": [],
                    "skill_version": "",
                    "result_archive_name": "",
                    "worker_id": row["worker_id"] or "",
                }
                conn.execute(
                    "update remote_executions set state = 'done', requeue_count = ?,"
                    " outcome_json = ?, updated_at = ? where execution_id = ?",
                    (requeue_count, json.dumps(outcome), now_ts, row["execution_id"]),
                )
                finished.append(row["execution_id"])
            else:
                conn.execute(
                    "update remote_executions set state = 'queued', worker_id = null,"
                    " requeue_count = ?, last_heartbeat_at = null, updated_at = ?"
                    " where execution_id = ?",
                    (requeue_count, now_ts, row["execution_id"]),
                )
        conn.execute(
            "delete from remote_executions where state = 'done' and updated_at < ?",
            (done_cutoff,),
        )
    return finished


class EntryHandle:
    """Write-through handle to one queue row.

    Legacy unit tests age claims via ``broker._entries[id].last_heartbeat_at``
    and read ``requeue_count`` back; keep that attribute working against the
    sqlite-backed queue. Production code must use the functions above.
    """

    _FIELDS = frozenset({"requeue_count", "last_heartbeat_at"})

    def __init__(self, db_path: Path, execution_id: str) -> None:
        object.__setattr__(self, "_db_path", db_path)
        object.__setattr__(self, "_execution_id", execution_id)

    def __getattr__(self, name: str) -> Any:
        if name not in self._FIELDS:
            raise AttributeError(name)
        with read_connection(self._db_path) as conn:
            row = conn.execute(
                f"select {name} from remote_executions where execution_id = ?",
                (self._execution_id,),
            ).fetchone()
        if row is None:
            raise AttributeError(name)
        value = row[name]
        if name == "last_heartbeat_at" and value is not None:
            value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._FIELDS:
            object.__setattr__(self, name, value)
            return
        if name == "last_heartbeat_at" and value is not None:
            value = _sqlite_timestamp(value)
        with write_transaction(self._db_path) as conn:
            conn.execute(
                f"update remote_executions set {name} = ? where execution_id = ?",
                (value, self._execution_id),
            )


class EntriesView:
    """Dict-like access to queue-row handles (legacy unit tests only)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def __getitem__(self, execution_id: str) -> EntryHandle:
        return EntryHandle(self._db_path, execution_id)
