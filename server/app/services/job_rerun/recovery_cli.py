"""Command-line wiring for dangling artifact dry-run and constrained apply."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.app.jobs.queries.artifact_dangling import artifact_dangling_reader_from_dsn
from server.app.services.artifact_dangling_plan import (
    DEFAULT_MAX_CANDIDATE_RATIO,
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_PAGE_SIZE,
    ArtifactDanglingQueries,
    build_artifact_dangling_plan,
    render_artifact_dangling_plan,
)
from server.app.services.artifact_dangling_probe import (
    ArtifactDanglingStorage,
    ArtifactHashLimits,
    s3_configuration_fingerprint,
)
from server.app.services.artifact_dangling_recovery import (
    ArtifactRecoveryError,
    execute_apply,
    load_reviewed_plan,
    prepare_apply,
    render_apply_report,
)
from server.app.services.job_rerun.recovery_hash import (
    DEFAULT_HASH_MAX_CONTENT_BYTES,
    DEFAULT_HASH_MAX_SECONDS,
    DEFAULT_HASH_MAX_STORED_BYTES,
)

ROOT = Path(__file__).resolve().parents[4]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover dangling job artifact manifests")
    parser.add_argument("--workspace", action="append", dest="workspace_ids")
    parser.add_argument("--job", action="append", dest="job_ids")
    parser.add_argument("--page-size", type=int)
    parser.add_argument("--output", type=Path, help="Write the plan/report here (default: stdout).")
    parser.add_argument("--database-url", help="Override AGENT_LEGION_DATABASE_URL.")
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--max-candidate-ratio", type=float)
    parser.add_argument(
        "--calibrate-thresholds",
        action="store_true",
        help="Mark both explicit thresholds as reviewed for later apply.",
    )
    parser.add_argument("--hash-max-stored-bytes", type=int)
    parser.add_argument("--hash-max-content-bytes", type=int)
    parser.add_argument("--hash-max-seconds", type=float, help="0 disables the deadline.")
    parser.add_argument("--apply", type=Path, metavar="PLAN_JSON")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--expected-bucket")
    parser.add_argument("--expected-endpoint")
    return parser


def _limits(args: argparse.Namespace) -> ArtifactHashLimits:
    seconds = args.hash_max_seconds
    return ArtifactHashLimits(
        max_stored_bytes=(
            args.hash_max_stored_bytes
            if args.hash_max_stored_bytes is not None
            else DEFAULT_HASH_MAX_STORED_BYTES
        ),
        max_content_bytes=(
            args.hash_max_content_bytes
            if args.hash_max_content_bytes is not None
            else DEFAULT_HASH_MAX_CONTENT_BYTES
        ),
        max_seconds=(
            None if seconds == 0 else seconds if seconds is not None else DEFAULT_HASH_MAX_SECONDS
        ),
    )


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    page_size = args.page_size if args.page_size is not None else DEFAULT_PAGE_SIZE
    if not 1 <= page_size <= 500:
        parser.error("--page-size must be between 1 and 500")
    if args.calibrate_thresholds and (
        args.max_candidates is None or args.max_candidate_ratio is None
    ):
        parser.error("--calibrate-thresholds requires both explicit threshold values")
    try:
        _limits(args)
    except ValueError as exc:
        parser.error(str(exc))
    apply_options = (args.plan_sha256, args.expected_bucket, args.expected_endpoint)
    if args.apply is None and any(value is not None for value in apply_options):
        parser.error("apply identity options require --apply PLAN_JSON")
    if args.apply is None:
        return
    if any(value is None for value in apply_options):
        parser.error("--apply requires --plan-sha256, --expected-bucket and --expected-endpoint")
    if (
        any(
            value is not None
            for value in (
                args.workspace_ids,
                args.job_ids,
                args.page_size,
                args.max_candidates,
                args.max_candidate_ratio,
                args.hash_max_stored_bytes,
                args.hash_max_content_bytes,
                args.hash_max_seconds,
            )
        )
        or args.calibrate_thresholds
    ):
        parser.error("--apply takes all scan, threshold and hash limits from the reviewed plan")


def _dry_run(
    args: argparse.Namespace,
    queries: ArtifactDanglingQueries,
    storage: ArtifactDanglingStorage,
    fingerprint: dict[str, str],
) -> int:
    plan = build_artifact_dangling_plan(
        queries,
        storage,
        workspace_ids=args.workspace_ids,
        job_ids=args.job_ids,
        page_size=args.page_size if args.page_size is not None else DEFAULT_PAGE_SIZE,
        max_candidates=(
            args.max_candidates if args.max_candidates is not None else DEFAULT_MAX_CANDIDATES
        ),
        max_candidate_ratio=(
            args.max_candidate_ratio
            if args.max_candidate_ratio is not None
            else DEFAULT_MAX_CANDIDATE_RATIO
        ),
        thresholds_calibrated=args.calibrate_thresholds,
        hash_limits=_limits(args),
        configuration=fingerprint,
    )
    _write(render_artifact_dangling_plan(plan), args.output)
    summary = plan["summary"]
    print(
        f"dry-run only: {summary['candidate_count']} candidate(s), "
        f"{summary['unknown_count']} unknown, {summary['actionable_count']} in-scope; "
        f"health_passed={plan['health']['passed']}; plan_sha256={plan['plan_sha256']}",
        file=sys.stderr,
    )
    return 0 if plan["health"]["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from server.app.services.artifact_dangling_probe import S3ArtifactDanglingStorage
    from server.app.settings import load_settings
    from server.app.storage.s3_settings import load_s3_settings

    settings = load_settings()
    s3_settings = load_s3_settings()
    if s3_settings is None:
        print("AGENT_LEGION_S3_BUCKET is required; no scan was attempted", file=sys.stderr)
        return 2
    dsn = args.database_url or settings.database_url
    storage = S3ArtifactDanglingStorage(s3_settings)
    fingerprint = s3_configuration_fingerprint(s3_settings)
    queries = artifact_dangling_reader_from_dsn(dsn)
    if args.apply is None:
        return _dry_run(args, queries, storage, fingerprint)
    try:
        reviewed = load_reviewed_plan(args.apply, args.plan_sha256)
        prepared = prepare_apply(
            queries,
            storage,
            reviewed,
            s3_settings,
            expected_bucket=args.expected_bucket,
            expected_endpoint=args.expected_endpoint,
        )
    except ArtifactRecoveryError as exc:
        print(f"apply refused: {exc.reason_code}: {exc}; applied=0", file=sys.stderr)
        return 1

    from server.app.executors.leases import ExecutorLeaseRepository
    from server.app.jobs import JobQueries
    from server.app.services.job_artifact_objects import JobArtifactObjectStore
    from server.app.services.job_rerun import JobRerunService

    writer = JobQueries(dsn, settings.jobs_dir)
    object_store = JobArtifactObjectStore(writer, storage)
    rerun_service = JobRerunService(
        writer,
        ExecutorLeaseRepository(writer, data_dir=settings.data_dir),
        settings,
        object_store=object_store,
    )
    report = execute_apply(prepared, rerun_service, storage)
    _write(render_apply_report(report), args.output)
    summary = report["summary"]
    print(
        f"apply finished: applied={summary['applied_count']}, "
        f"not_applied={summary['not_applied_count']}, "
        f"stopped={report['batch']['stopped_after_failure']}",
        file=sys.stderr,
    )
    return 0 if summary["not_applied_count"] == 0 else 1
