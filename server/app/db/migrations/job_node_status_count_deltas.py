"""Schema v88: non-blocking delta folds for the job NODE status counters (#690).

v82 (#659) rebuilt the job-level counter families (workspace_job_status_counts
/ run_job_status_counts) as append-and-fold, but the node-level sibling kept
its v56 shape: a FOR EACH ROW trigger on job_nodes upserting the shared
(workspace_id, node_key, status) row once per changed node. A claim batch
promoting several nodes, a result-commit wave and the jobs deduct/rekey
triggers each take those hot rows in business order, so two transactions in
one workspace close an AB-BA ring on the counter rows (``ShareLock on
transaction`` / ``inserting index tuple``), and every transaction queued
behind either side — batched heartbeats included — inherits the 40P01.

A blocking per-workspace advisory gate at the trigger entry does not fix
this (v82's docstring records why): a row-level AFTER trigger fires while
its statement already holds the job_nodes row lock, so waiting on the gate
adds a row-lock × advisory edge that closes a new ring. v88 instead applies
v82's protocol to the node family: ``pg_try_advisory_xact_lock(88, ws)``
elects one folder per workspace that alone writes the base rows until its
transaction ends; every other writer appends an insert-only delta row and
moves on. No acquisition anywhere in the family waits, so the family adds no
wait-for edge at all — which is also why its lock class needs no ordering
relative to v82's classes 82/83.

DDL home: the delta table and the three functions live only in the sibling
``.sql`` (the schema file sits at its raw-line ceiling — the v77/v78 rule),
and ``bump_job_node_status_counts`` moved here out of postgres_schema.sql so
that a future upgrade's schema-file replay can never restore the blocking
pre-v88 body (the replay runs on every upgrade, this migration only once).
The schema file keeps the table, the row/deduct/rekey trigger functions —
now delegating every write to ``bump_job_node_status_counts`` — and the
trigger DDL the pre-v70 upgrade chain depends on. On every install path no
job_nodes write runs between the schema replay and this migration, so the
not-yet-installed bump body is never reached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_MIGRATION_SQL = Path(__file__).with_suffix(".sql")


def migrate_job_node_status_count_deltas(conn: Any) -> None:
    """Install v88's node delta table, try-lock folder, and bump entry."""
    conn.execute(_MIGRATION_SQL.read_text(encoding="utf-8"))
