"""Read-only historical Agent-route retirement audit (#936, #440 M27-E).

Scans every job snapshot, active unresolved revision, historical Agent-version
pin and queued Agent-definition request. The report classifies equivalent and
route-less fallbacks separately from identity drift, unavailable targets, pin
mismatches and unreadable snapshots. Any blocking item exits 1; no route,
definition, request, schema or runtime state is changed.

Connections enforce ``default_transaction_read_only=on`` at the server and
never run ``init_db``. Reports contain only local execution metadata, never
snapshot/manifest/config/input bodies; keep output files local and uncommitted.

Usage:
    uv run python -m scripts.agent_route_retirement_audit
        [--output PATH] [--database-url POSTGRESQL_URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.app.jobs.queries.agent_backfill_reader import agent_backfill_reader_from_dsn
from server.app.services.agent_backfill_report import render_report_json
from server.app.services.agent_route_retirement_audit import (
    build_agent_route_retirement_audit,
)
from server.app.settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the JSON report to this local file (default: stdout).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured AGENT_LEGION_DATABASE_URL (a derived/read replica).",
    )
    args = parser.parse_args(argv)

    dsn = args.database_url or load_settings().database_url
    report = build_agent_route_retirement_audit(agent_backfill_reader_from_dsn(dsn))
    text = render_report_json(report)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
        print(
            f"wrote {args.output}: {report['summary']['blocking_items']} blocking item(s); "
            "read-only audit, no changes applied",
            file=sys.stderr,
        )
    return 1 if report["route_retirement_blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
