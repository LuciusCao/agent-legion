"""On-disk pass log for the workflow worker.

The app never configures the root logger, so under uvicorn the worker's INFO
records never reach a handler; and the pass summary only prints at pass end,
leaving a stuck pass invisible. This dedicated file logger makes pass cadence
and claims observable on disk without touching the logging configuration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections import deque

    from server.app.settings import Settings

_LOGGERS: dict[str, logging.Logger] = {}


def pass_logger(settings: Settings) -> logging.Logger:
    """Return the pass logger for this settings' logs dir (created once)."""
    log_path = settings.logs_dir / "workflow_worker_pass.log"
    key = str(log_path)
    cached = _LOGGERS.get(key)
    if cached is not None:
        return cached
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("server.app.workflow_worker_pass")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    _LOGGERS[key] = logger
    return logger


def log_pass_start(
    logger: logging.Logger,
    jobs_by_workspace: dict[str, list[Any]],
    queues: dict[str, deque[Any]],
    scan_seconds: float,
) -> None:
    logger.info(
        "pass start: jobs=%d workspaces=%d candidates=%d scan=%.2fs",
        sum(len(v) for v in jobs_by_workspace.values()),
        len(queues),
        sum(len(queue) for queue in queues.values()),
        scan_seconds,
    )


def log_pass_end(
    logger: logging.Logger,
    *,
    scan_seconds: float,
    jobs: int,
    ready_stats: dict[str, int],
    claims: int,
    candidates: int,
    claim_seconds: float,
    claim_counts: dict[str, int],
    stock_gated: int = 0,
    scan_phases: dict[str, float] | None = None,
) -> None:
    # Phase attribution for the scan segment; "py" is the unaccounted
    # remainder (pure-Python mark filtering / candidate assembly / pruning).
    phases = scan_phases or {}
    accounted = sum(phases.get(key, 0.0) for key in ("marks", "ws_query", "miss_fetch", "eval"))
    py_seconds = max(scan_seconds - accounted, 0.0)
    logger.info(
        "pass end: scan=%.2fs (marks=%.2f ws=%.2f fetch=%.2f eval=%.2f py=%.2f)"
        " jobs=%d ready_cache hit=%d miss=%d running_jobs=%d"
        " claims=%d candidates=%d claim_loop=%.2fs stock_gated=%d by_target=%s",
        scan_seconds,
        phases.get("marks", 0.0),
        phases.get("ws_query", 0.0),
        phases.get("miss_fetch", 0.0),
        phases.get("eval", 0.0),
        py_seconds,
        jobs,
        ready_stats.get("hit", 0),
        ready_stats.get("miss", 0),
        ready_stats.get("running", 0),
        claims,
        candidates,
        claim_seconds,
        stock_gated,
        dict(sorted(claim_counts.items())),
    )
