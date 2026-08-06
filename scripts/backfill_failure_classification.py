"""Backfill failure classification for historical failed node runs.

Scans ``node_runs`` rows with ``status='failed'`` and an empty
``failure_category`` and fills ``failure_category``/``failure_detail`` via
``server.app.services.failure_classification.classify_failure``. Idempotent:
rows that already carry a classification are never touched, and the UPDATE
re-checks the predicates so rows classified concurrently are left alone.
With ``--include-unknown`` rows previously classified as ``unknown`` are
re-evaluated too — useful after the rule table learns new patterns.

Usage:
    uv run python -m scripts.backfill_failure_classification [--dry-run] [--include-unknown]
"""

from __future__ import annotations

import argparse

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.failure_classification import classify_failure
from server.app.settings import load_settings


def backfill_failure_classification(
    database_dsn: DatabaseDsn, *, dry_run: bool = False, include_unknown: bool = False
) -> tuple[int, int]:
    """Return (matched, updated) row counts for unclassified failed runs."""
    predicate = "failure_category='' or failure_category='unknown'"
    if not include_unknown:
        predicate = "failure_category=''"
    with read_connection(database_dsn) as conn:
        rows = conn.execute(
            f"""
            select id, exit_code, error_message, failure_category, failure_detail
            from node_runs
            where status='failed' and ({predicate})
            order by id
            """
        ).fetchall()

    planned = []
    for row in rows:
        category, detail = classify_failure(row["exit_code"], str(row["error_message"]))
        if (category, detail) != (row["failure_category"], row["failure_detail"]):
            planned.append((int(row["id"]), category, detail))
    if dry_run:
        return len(planned), 0

    updated = 0
    with write_transaction(database_dsn) as conn:
        for run_id, category, detail in planned:
            cursor = conn.execute(
                f"""
                update node_runs
                set failure_category=%s, failure_detail=%s
                where id=%s and status='failed' and ({predicate})
                """,
                (category, detail, run_id),
            )
            updated += cursor.rowcount
    return len(planned), updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report how many rows would be updated.",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="Also re-evaluate rows currently classified as 'unknown'.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured AGENT_LEGION_DATABASE_URL.",
    )
    args = parser.parse_args()

    dsn = args.database_url or load_settings().database_url
    matched, updated = backfill_failure_classification(
        dsn, dry_run=args.dry_run, include_unknown=args.include_unknown
    )
    if args.dry_run:
        print(f"dry-run: {matched} failed node run(s) would be classified")
    else:
        print(f"backfilled {updated} of {matched} unclassified failed node run(s)")


if __name__ == "__main__":
    main()
