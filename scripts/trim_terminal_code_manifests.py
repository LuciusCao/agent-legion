"""Slim terminal kind='code' manifests to lightweight audit stubs (issue #142).

Production 止血 for the ``agent_execution_requests`` TOAST bloat: every code
row enqueued before the fix embeds a full ~1.7MB ``runtime_context`` (the
intake ``job_batch`` payload), ~198G across the table. This script replaces
that runtime_context with the lightweight audit stub (job/workspace ids,
batch_id + hash) on all terminal (``done``/``cancelled``) code rows, in
bounded batches so row locks stay short. Idempotent: re-running touches
nothing (stub rows carry no heavy ``runtime_context.job``).

Actual disk reclamation is an ops step AFTER this script — ``VACUUM FULL`` /
``pg_repack`` at low peak (VACUUM FULL locks the table). New code rows never
grow again: enqueue persists only the stub and the claim response rebuilds
the payloads in memory; terminal transitions slim automatically
(``server/app/agent_broker/code_manifest.py``).

Usage:
    uv run python -m scripts.trim_terminal_code_manifests [--dry-run] \
        [--database-url DSN] [--batch-size N]
"""

from __future__ import annotations

import argparse
import logging

from server.app.agent_broker.code_manifest import CODE_MANIFEST_TRIM
from server.app.db.transaction import write_transaction
from server.app.settings import load_settings

logger = logging.getLogger(__name__)

# The heavy legacy context always carries the embedded job dict; the stub
# rows introduced by the fix never do. Existence is implied by the
# ``#>> '{runtime_context,job}'`` path — the jsonb ``?`` operator is banned
# by the repo's dialect guard (server/app/db/dialect.py).
_HEAVY_CONTEXT_WHERE = """
kind = 'code'
and state in ('done', 'cancelled')
and manifest_json::jsonb #>> '{runtime_context,job}' is not null
"""


def trim_terminal_code_manifests(
    database_dsn: str, *, dry_run: bool = False, batch_size: int = 500
) -> int:
    """Slim terminal legacy code rows in bounded batches; return rows touched."""
    touched = 0
    while True:
        with write_transaction(database_dsn) as conn:
            if dry_run:
                row = conn.execute(
                    "select count(*) as cnt from agent_execution_requests where "
                    + _HEAVY_CONTEXT_WHERE
                ).fetchone()
                return int(row["cnt"]) if row else 0
            updated = conn.execute(
                "update agent_execution_requests set manifest_json="
                + CODE_MANIFEST_TRIM
                + " where execution_id in ("
                " select execution_id from agent_execution_requests where "
                + _HEAVY_CONTEXT_WHERE
                + f" limit {int(batch_size)}"
                ")"
            )
        batch_touched = updated.rowcount
        touched += batch_touched
        if batch_touched < batch_size:
            break
    return touched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not update.")
    parser.add_argument("--database-url", default=None, help="Override configured DSN.")
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Rows slimmed per transaction (default 500)."
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    touched = trim_terminal_code_manifests(dsn, dry_run=args.dry_run, batch_size=args.batch_size)
    print(f"{'would slim' if args.dry_run else 'slimmed'} {touched} terminal code manifest(s)")
    if not args.dry_run:
        print(
            "note: disk is reclaimed only by an ops-side VACUUM FULL / pg_repack"
            " at low peak (VACUUM FULL locks agent_execution_requests)"
        )


if __name__ == "__main__":
    main()
