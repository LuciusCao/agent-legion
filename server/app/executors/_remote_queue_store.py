"""PostgreSQL write/read paths for the remote execution queue.

Each write function owns its connect-and-transact unit via
``server.app.db.transaction.write_transaction`` and is decorated with
PostgreSQL transaction conflicts retry the whole unit on a fresh connection.
``outcome_json`` holds ``dataclasses.asdict(RemoteOutcome)`` with the
``command`` tuple serialized as a JSON list.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.db.retry import retried_on_database_conflict
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_transactions import _database_timestamp

_DONE_RETENTION = timedelta(hours=24)


def labels_satisfy(labels: Mapping[str, Any], requirements: Mapping[str, str]) -> bool:
    """Evaluate capability ``requires_labels`` constraints against worker labels.

    Each constraint value is either ``">=<int>"`` (numeric comparison; the
    label value must be float-convertible) or a literal matched by string
    equality. An unknown label never satisfies its constraint.
    """
    for key, req in requirements.items():
        value = labels.get(key)
        if value is None:
            return False
        if req.startswith(">="):
            try:
                if float(value) < float(req[2:]):
                    return False
            except (TypeError, ValueError):
                return False
        elif str(value) != req:
            return False
    return True


@retried_on_database_conflict
def claim_next(
    db_path: str,
    worker_id: str,
    capabilities: Collection[str],
    now: str,
    *,
    label_requirements: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Claim the oldest eligible row without exceeding the worker's slots."""
    if not capabilities:
        return None
    with write_transaction(db_path) as conn:
        # Prevent concurrent requests from oversubscribing this worker's slot
        # count while allowing different workers to dequeue in parallel.
        conn.execute(
            "select pg_advisory_xact_lock(hashtext(?))",
            (f"remote-worker-claim:{worker_id}",),
        )
        row = conn.execute(
            "select slots, labels_json from remote_workers where worker_id = ?", (worker_id,)
        ).fetchone()
        worker_labels: Mapping[str, Any] = {}
        if row is not None:
            held_row = conn.execute(
                "select count(*) as cnt from remote_executions"
                " where state = 'claimed' and worker_id = ?",
                (worker_id,),
            ).fetchone()
            held = int(held_row["cnt"]) if held_row is not None else 0
            if held >= row["slots"]:
                return None
            worker_labels = json.loads(row["labels_json"] or "{}")
        eligible = [
            capability
            for capability in capabilities
            if labels_satisfy(
                worker_labels,
                (label_requirements or {}).get(capability, {}),
            )
        ]
        if not eligible:
            return None
        candidate = conn.execute(
            "select execution_id from remote_executions"
            " where state = 'queued' and capability = any(?)"
            " order by created_at, execution_id for update skip locked limit 1",
            (eligible,),
        ).fetchone()
        if candidate is None:
            return None
        entry = conn.execute(
            "update remote_executions set state = 'claimed', worker_id = ?,"
            " last_heartbeat_at = ?, updated_at = ? where execution_id = ?"
            " returning *",
            (worker_id, now, now, candidate["execution_id"]),
        ).fetchone()
        return dict(entry) if entry is not None else None


@retried_on_database_conflict
def heartbeat_claim(db_path: str, execution_id: str, worker_id: str, now: str) -> bool:
    with write_transaction(db_path) as conn:
        cursor = conn.execute(
            "update remote_executions set last_heartbeat_at = ?, updated_at = ?"
            " where execution_id = ? and state = 'claimed' and worker_id = ?",
            (now, now, execution_id, worker_id),
        )
        return cursor.rowcount > 0


@retried_on_database_conflict
def finish_execution(
    db_path: str,
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


@retried_on_database_conflict
def cancel_execution(
    db_path: str, execution_id: str, outcome_dict: dict[str, Any], now: str
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


def bundle_name_for_claim(db_path: str, execution_id: str, worker_id: str) -> str | None:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select bundle_name from remote_executions"
            " where execution_id = ? and state = 'claimed' and worker_id = ?",
            (execution_id, worker_id),
        ).fetchone()
    return row["bundle_name"] if row is not None else None


@retried_on_database_conflict
def sweep(
    db_path: str, *, now: datetime, claim_timeout: timedelta, requeue_limit: int
) -> list[str]:
    """Requeue stale claims, fail rows past the requeue limit, and delete done
    rows older than the retention window. Returns execution ids failed here."""
    now_ts = _database_timestamp(now)
    cutoff = _database_timestamp(now - claim_timeout)
    done_cutoff = _database_timestamp(now - _DONE_RETENTION)
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
    PostgreSQL-backed queue. Production code must use the functions above.
    """

    _FIELDS = frozenset({"requeue_count", "last_heartbeat_at"})

    def __init__(self, db_path: str, execution_id: str) -> None:
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
        if name == "last_heartbeat_at" and value is not None and not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._FIELDS:
            object.__setattr__(self, name, value)
            return
        if name == "last_heartbeat_at" and value is not None:
            value = _database_timestamp(value)
        with write_transaction(self._db_path) as conn:
            conn.execute(
                f"update remote_executions set {name} = ? where execution_id = ?",
                (value, self._execution_id),
            )


class EntriesView:
    """Dict-like access to queue-row handles (legacy unit tests only)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def __getitem__(self, execution_id: str) -> EntryHandle:
        return EntryHandle(self._db_path, execution_id)
