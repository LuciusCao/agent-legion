"""Read-only dangling job-artifact recovery plan (#1019).

Scans latest ``job_artifacts`` rows, proves object-store health, and emits a
machine-readable rerun plan. This M4 tool has no apply mode: it never deletes
manifest rows or objects, reruns jobs, clears hydration notices, initializes
the schema, or resumes scheduling. Every DB connection is forced to
``default_transaction_read_only=on``.

Usage:
    uv run python -m scripts.clean_dangling_job_artifacts
        [--workspace ID ...] [--job ID ...] [--page-size 100]
        [--output PATH] [--database-url ...]

The report contains deployment identifiers and object keys; keep it local.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.app.jobs.queries.artifact_dangling import artifact_dangling_reader_from_dsn
from server.app.services.artifact_dangling_plan import (
    DEFAULT_PAGE_SIZE,
    build_artifact_dangling_plan,
    render_artifact_dangling_plan,
)

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace",
        action="append",
        dest="workspace_ids",
        help="Limit the scan to this workspace id (repeatable; default: all).",
    )
    parser.add_argument(
        "--job",
        action="append",
        dest="job_ids",
        help="Limit the scan to this job id (repeatable; default: all).",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--output", type=Path, help="Write the JSON plan here (default: stdout).")
    parser.add_argument("--database-url", help="Override AGENT_LEGION_DATABASE_URL.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.page_size <= 500:
        _parser().error("--page-size must be between 1 and 500")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from server.app.services.artifact_dangling_probe import (
        S3ArtifactDanglingStorage,
        s3_configuration_fingerprint,
    )
    from server.app.settings import load_settings
    from server.app.storage.s3_settings import load_s3_settings

    settings = load_settings()
    s3_settings = load_s3_settings()
    if s3_settings is None:
        print("AGENT_LEGION_S3_BUCKET is required; no scan was attempted", file=sys.stderr)
        return 2
    storage = S3ArtifactDanglingStorage(s3_settings)
    queries = artifact_dangling_reader_from_dsn(args.database_url or settings.database_url)
    plan = build_artifact_dangling_plan(
        queries,
        storage,
        workspace_ids=args.workspace_ids,
        job_ids=args.job_ids,
        page_size=args.page_size,
        configuration=s3_configuration_fingerprint(s3_settings),
    )
    text = render_artifact_dangling_plan(plan)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    summary = plan["summary"]
    print(
        "dry-run only: "
        f"{summary['candidate_count']} candidate(s), {summary['unknown_count']} unknown, "
        f"{summary['actionable_count']} in-scope; health_passed={plan['health']['passed']}; "
        f"plan_sha256={plan['plan_sha256']}",
        file=sys.stderr,
    )
    return 0 if plan["health"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
