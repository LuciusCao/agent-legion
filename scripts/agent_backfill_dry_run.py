"""Agent definition retirement: backfill dry-run report (#934, #440).

Read-only. Simulates the 0.7.17 backfill (agent definition fields → agent
nodes of every workspace's active revision and Studio draft) and prints
what it would do: per-node runtime / tools / config_schema / skill /
requires_labels, shared-definition groups (1:N expansion), unresolved
nodes, and config_schema overwrites that discard a node declaration.

Zero writes by construction: every connection is opened with
``default_transaction_read_only=on`` and the schema bootstrap (``init_db``)
never runs; no object storage is touched.

The report reflects a deployment's data — keep it local, never commit it.

Usage:
    uv run python -m scripts.agent_backfill_dry_run [--workspace ID ...]
        [--output PATH] [--database-url ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.app.jobs.queries.agent_backfill_reader import agent_backfill_reader_from_dsn
from server.app.services.agent_backfill_report import (
    build_agent_backfill_report,
    render_report_json,
)
from server.app.settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace",
        action="append",
        dest="workspaces",
        help="Limit the report to this workspace id (repeatable; default: all).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the JSON report to this local file (default: stdout).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured AGENT_LEGION_DATABASE_URL.",
    )
    args = parser.parse_args(argv)

    dsn = args.database_url or load_settings().database_url
    report = build_agent_backfill_report(agent_backfill_reader_from_dsn(dsn), args.workspaces)
    text = render_report_json(report)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
        summary = report["summary"]
        print(
            f"wrote {args.output}: {summary['agent_nodes_backfilled']} backfilled,"
            f" {summary['agent_nodes_unresolved']} unresolved,"
            f" {summary['shared_definition_groups']} shared group(s),"
            f" {summary['config_schema_overrides']} config_schema override(s)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
