"""Per-partition coverage floor report (non-blocking by default).

The global 85% floor lets critical modules hide behind high-coverage averages.
This report pins named partitions (key modules/directories) to their own line
floors so a regression in one of them is visible on its own. It runs in report
mode everywhere; pass ``--enforce`` (or set ``AGENT_LEGION_COV_PARTITIONS=enforce``)
to turn violations into a non-zero exit once the floors are proven stable.

Data sources:
- backend: a coverage.py data file (combined quick+full rounds in check.sh),
  converted through ``coverage json`` for per-file line totals.
- frontend: ``frontend/coverage/coverage-final.json`` (V8/istanbul), where line
  coverage is derived from zero-hit statement spans.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Partition:
    name: str
    source: str  # "backend" | "frontend"
    prefixes: tuple[str, ...]
    min_lines: float


# Floors follow Phase 3 of the 2026-08 test architecture plan:
# key modules at >=80% lines with behavior assertions, former <60% modules at >=70%.
PARTITIONS: tuple[Partition, ...] = (
    Partition(
        "backend agent dispatch",
        "backend",
        ("server/app/agent_broker/dispatch.py", "server/app/agent_broker/dispatch_pool.py"),
        80.0,
    ),
    Partition(
        "backend workflow upgrade",
        "backend",
        (
            "server/app/routes/job_workflow_upgrade.py",
            "server/app/services/job_workflow_upgrade.py",
        ),
        80.0,
    ),
    Partition(
        "backend agent artifacts",
        "backend",
        ("server/app/agent_broker/agent_artifacts.py",),
        70.0,
    ),
    Partition(
        "backend worker package",
        "backend",
        ("worker/",),
        90.0,
    ),
    Partition(
        "backend skill version fallbacks",
        "backend",
        ("server/app/workflows/skill_version_fallbacks.py",),
        70.0,
    ),
    Partition(
        "backend job log raw",
        "backend",
        ("server/app/services/job_log_raw.py",),
        70.0,
    ),
    Partition(
        "frontend auth/bootstrap",
        "frontend",
        (
            "src/stores/authStore.ts",
            "src/pages/LoginPage.tsx",
            "src/pages/SetupPage.tsx",
            "src/App.tsx",
            "src/main.tsx",
        ),
        80.0,
    ),
    Partition(
        "frontend api transport",
        "frontend",
        ("src/api/",),
        80.0,
    ),
    Partition(
        "frontend workflow upgrade",
        "frontend",
        (
            "src/api/jobWorkflowUpgradeApi.ts",
            "src/pages/jobDetail/useUpgradeWorkflowAction.ts",
        ),
        80.0,
    ),
    Partition(
        "frontend admin pages",
        "frontend",
        ("src/pages/UsersAdminPage.tsx", "src/pages/JobDetailPage.tsx"),
        80.0,
    ),
)


def backend_line_totals(data_file: Path) -> dict[str, tuple[int, int]]:
    """Return {path: (covered_lines, total_lines)} from a coverage.py data file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        json_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "json",
                f"--data-file={data_file}",
                "-o",
                str(json_path),
                "--quiet",
            ],
            check=True,
            cwd=ROOT_DIR,
        )
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    finally:
        json_path.unlink(missing_ok=True)
    totals: dict[str, tuple[int, int]] = {}
    for path, entry in payload["files"].items():
        summary = entry["summary"]
        totals[path] = (summary["covered_lines"], summary["num_statements"])
    return totals


def frontend_line_totals(coverage_final: Path) -> dict[str, tuple[int, int]]:
    """Return {relpath: (covered_lines, total_lines)} from a V8 istanbul report."""
    payload = json.loads(coverage_final.read_text(encoding="utf-8"))
    totals: dict[str, tuple[int, int]] = {}
    for path, entry in payload.items():
        covered: set[int] = set()
        uncovered: set[int] = set()
        for stmt_id, count in entry["s"].items():
            loc = entry["statementMap"][stmt_id]
            lines = range(loc["start"]["line"], loc["end"]["line"] + 1)
            if count > 0:
                covered.update(lines)
            else:
                uncovered.update(lines)
        total = covered | uncovered
        if not total:
            continue
        rel = path.split("/src/", 1)[-1]
        totals[f"src/{rel}"] = (len(covered), len(total))
    return totals


def evaluate(
    partitions: tuple[Partition, ...],
    totals_by_source: dict[str, dict[str, tuple[int, int]]],
    provided_sources: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (report_rows, violation_messages) for each partition.

    Partitions whose source was not provided at all are reported as SKIP;
    a provided source with no matching files is a violation.
    """
    rows: list[str] = []
    violations: list[str] = []
    for partition in partitions:
        if provided_sources is not None and partition.source not in provided_sources:
            rows.append(f"{partition.name}: SKIP (no {partition.source} coverage data)")
            continue
        totals = totals_by_source[partition.source]
        covered = sum(c for path, (c, _t) in totals.items() if _matches(path, partition))
        total = sum(t for path, (_c, t) in totals.items() if _matches(path, partition))
        if total == 0:
            violations.append(f"{partition.name}: no matching files in coverage data")
            rows.append(f"{partition.name}: NO DATA (floor {partition.min_lines:.0f}%)")
            continue
        pct = covered / total * 100
        status = "OK" if pct >= partition.min_lines else "BELOW FLOOR"
        rows.append(
            f"{partition.name}: {pct:.1f}% lines ({covered}/{total}, "
            f"floor {partition.min_lines:.0f}%) {status}"
        )
        if pct < partition.min_lines:
            violations.append(
                f"{partition.name}: {pct:.1f}% lines below floor {partition.min_lines:.0f}%"
            )
    return rows, violations


def _matches(path: str, partition: Partition) -> bool:
    normalized = path.replace("\\", "/")
    for prefix in partition.prefixes:
        if prefix.endswith("/"):
            if normalized.startswith(prefix):
                return True
        elif normalized == prefix:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, help="coverage.py data file")
    parser.add_argument(
        "--frontend",
        type=Path,
        default=ROOT_DIR / "frontend" / "coverage" / "coverage-final.json",
        help="frontend coverage-final.json",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        default=os.environ.get("AGENT_LEGION_COV_PARTITIONS") == "enforce",
        help="exit non-zero when a partition is below its floor",
    )
    args = parser.parse_args(argv)

    totals_by_source: dict[str, dict[str, tuple[int, int]]] = {"backend": {}, "frontend": {}}
    provided_sources: set[str] = set()
    if args.backend is not None:
        totals_by_source["backend"] = backend_line_totals(args.backend)
        provided_sources.add("backend")
    if args.frontend.is_file():
        totals_by_source["frontend"] = frontend_line_totals(args.frontend)
        provided_sources.add("frontend")

    rows, violations = evaluate(PARTITIONS, totals_by_source, provided_sources)
    print("=== Coverage Partition Report ===")
    for row in rows:
        print(row)
    if violations:
        print("Coverage partition violations:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        if args.enforce:
            return 1
        print("(non-blocking; set AGENT_LEGION_COV_PARTITIONS=enforce to fail)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
