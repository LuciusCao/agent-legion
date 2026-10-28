"""Drive real Host/Worker register -> sandbox -> upload -> restart measurement runs.

Run on native Linux with Docker and the project Python environment. The only
LLM is a deterministic loopback stub; jobs, remote code claims, velites agent
execution, artifact uploads, and control-plane transitions are all real.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.stress.worker_resource_output import _positive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir", type=Path, default=Path("data/worker-resource-measurement/work")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("worker-resource-results/latest"))
    parser.add_argument("--velites-bin", type=Path, default=Path("data/bin/velites"))
    parser.add_argument("--agent-concurrency", type=_positive, default=2)
    parser.add_argument("--code-concurrency", type=int, default=2)
    parser.add_argument("--upload-concurrency", type=_positive, default=2)
    parser.add_argument("--mem-limit")
    parser.add_argument("--pids-limit", type=int)
    parser.add_argument("--claim-batch-limit", type=_positive, default=8)
    parser.add_argument("--jobs-per-wave", type=_positive, default=4)
    parser.add_argument("--artifact-kib", type=_positive, default=64)
    parser.add_argument("--stub-delay-seconds", type=float, default=0.05)
    parser.add_argument("--idle-seconds", type=float, default=2.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--backlog-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--pids-interval-ms", type=_positive, default=100)
    parser.add_argument("--memory-interval-ms", type=_positive, default=500)
    parser.add_argument("--cold-repetitions", type=int, default=1)
    parser.add_argument("--warm-repetitions", type=int, default=2)
    parser.add_argument("--postgres-port", type=int, default=55432)
    parser.add_argument("--s3-port", type=int, default=58333)
    parser.add_argument("--host-port", type=int, default=58080)
    parser.add_argument("--worker-port", type=int, default=58787)
    parser.add_argument("--stub-port", type=int, default=58081)
    parser.add_argument("--cgroup-reader-port", type=int, default=58082)
    parser.add_argument("--allow-docker-desktop", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args(argv)
    if args.jobs_per_wave < 2:
        parser.error("jobs-per-wave must be >= 2 for simultaneous active and queued uploads")
    if args.code_concurrency < 0:
        parser.error("code-concurrency must be non-negative")
    if args.pids_limit is not None and args.pids_limit != -1 and args.pids_limit <= 0:
        parser.error("pids-limit must be positive or -1")
    if args.mem_limit is not None and not args.mem_limit.strip():
        parser.error("mem-limit must not be empty")
    if (
        args.cold_repetitions < 0
        or args.warm_repetitions < 0
        or args.cold_repetitions + args.warm_repetitions == 0
    ):
        parser.error("cold/warm repetitions must be non-negative and not both zero")
    if (
        args.stub_delay_seconds < 0
        or args.idle_seconds < 0
        or args.job_timeout_seconds <= 0
        or args.backlog_timeout_seconds <= 0
    ):
        parser.error("delays must be non-negative and timeouts must be positive")
    args.artifact_chars = args.artifact_kib * 1024
    return args
