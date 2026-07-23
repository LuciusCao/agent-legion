#!/usr/bin/env python3
"""Compare token cost and retry behavior by skill version.

Usage: uv run python -m scripts.compare_skill_cost --jobs jobs.txt [--node NODE] [--version VER] ...
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._skill_cost_core import (
    ZERO_STATS,
    aggregate_job,
    compute_costs,
    fmt_cny,
    fmt_num,
    load_pricing,
    pct_change,
    resolve_job_paths,
)


def _row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"


def _print_report(jobs: list[dict[str, Any]], pricing: dict[str, float | str]) -> None:
    node_versions: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: dict(ZERO_STATS))
    )
    version_totals: dict[str, dict[str, int]] = defaultdict(lambda: dict(ZERO_STATS))
    for job in jobs:
        for (node, version), stats in job["nodes"].items():
            for key, value in stats.items():
                node_versions[node][version][key] += value
                version_totals[version][key] += value

    def avg(cost: float, count: int) -> str:
        return fmt_cny(cost / count) if count else "N/A"

    print(f"# Skill Cost Comparison by Version ({pricing['model']})\n")
    print("## Pricing")
    print(
        f"- Input: {pricing['input_per_1m']} CNY / 1M tokens\n"
        f"- Output: {pricing['output_per_1m']} CNY / 1M tokens\n"
        f"- Cache read: {pricing['cache_read_per_1m']} CNY / 1M tokens\n"
    )

    print("## Per-Node Comparison\n")
    headers = [
        "Node",
        "Skill Version",
        "Runs",
        "Messages",
        "Input",
        "Output",
        "CacheRead",
        "Billable",
        "Total",
        "Avg/Run",
    ]
    print(_row(*headers))
    print(_row(*["-" * len(h) for h in headers]))
    for node in sorted(node_versions):
        for version in sorted(node_versions[node]):
            stats = node_versions[node][version]
            costs = compute_costs(stats, pricing)
            print(
                _row(
                    node,
                    version,
                    fmt_num(stats["runs"]),
                    fmt_num(stats["messages"]),
                    fmt_num(stats["input"]),
                    fmt_num(stats["output"]),
                    fmt_num(stats["cache_read"]),
                    fmt_cny(costs["billable"]),
                    fmt_cny(costs["total"]),
                    avg(costs["total"], stats["runs"]),
                )
            )

    print("\n## Overall Cost by Version\n")
    headers = [
        "Skill Version",
        "Runs",
        "Messages",
        "Input",
        "Output",
        "CacheRead",
        "Billable",
        "Total",
        "Avg/Run",
    ]
    print(_row(*headers))
    print(_row(*["-" * len(h) for h in headers]))
    version_costs = {
        version: compute_costs(stats, pricing) for version, stats in version_totals.items()
    }
    for version in sorted(version_totals):
        stats = version_totals[version]
        costs = version_costs[version]
        print(
            _row(
                version,
                fmt_num(stats["runs"]),
                fmt_num(stats["messages"]),
                fmt_num(stats["input"]),
                fmt_num(stats["output"]),
                fmt_num(stats["cache_read"]),
                fmt_cny(costs["billable"]),
                fmt_cny(costs["total"]),
                avg(costs["total"], stats["runs"]),
            )
        )

    if len(version_costs) == 2:
        older, newer = sorted(version_costs)
        old_avg = version_costs[older]["total"] / version_totals[older]["runs"]
        new_avg = version_costs[newer]["total"] / version_totals[newer]["runs"]
        print(f"\n- {older} avg/run: {fmt_cny(old_avg)} CNY")
        print(f"- {newer} avg/run: {fmt_cny(new_avg)} CNY")
        print(f"- Change (avg/run): {pct_change(old_avg, new_avg)}")

    print(f"\n- Jobs analyzed: {len(jobs)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs", required=True, help="Glob pattern or file listing job directories."
    )
    parser.add_argument("--node", help="Filter to node names containing this substring.")
    parser.add_argument(
        "--version",
        action="append",
        dest="versions",
        help="Filter to versions containing this substring (can repeat).",
    )
    parser.add_argument("--pricing", type=Path, help="JSON pricing config file.")
    args = parser.parse_args(argv)

    paths = resolve_job_paths(args.jobs)
    if not paths:
        print(f"No job directories resolved from: {args.jobs}", file=sys.stderr)
        return 1
    jobs = [
        job
        for job in (aggregate_job(p, args.node or "", args.versions or []) for p in paths)
        if job is not None
    ]
    if not jobs:
        print("No events.jsonl found in jobs matching filters.", file=sys.stderr)
        return 1

    _print_report(jobs, load_pricing(args.pricing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
