"""Canonical Host-code log and run-directory ownership validation."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

_DATA_ROOT = PurePosixPath("/var/lib/agent-legion")


def _log_path(row: dict[str, Any]) -> str:
    raw = PurePosixPath(row["log_path"])
    path = raw.relative_to(_DATA_ROOT) if raw.is_absolute() else raw
    parts = path.parts
    if (
        len(parts) != 6
        or parts[:3] != ("logs", "jobs", "by-job")
        or parts[4:]
        != (
            row["job_id"],
            f"{row['node_key']}.log",
        )
        or ".." in parts
    ):
        raise ValueError(f"rejected Host code log path: {path}")
    run_dir = PurePosixPath(row.get("run_dir") or "")
    run_dir = run_dir.relative_to(_DATA_ROOT) if run_dir.is_absolute() else run_dir
    if run_dir.parts[:5] != (
        "jobs",
        row["workspace_id"],
        row["job_id"],
        "runs",
        row["node_key"],
    ) or (len(run_dir.parts) != 6 or run_dir.parts[-1] in {"", ".", ".."}):
        raise ValueError(f"Host code run_dir ownership mismatch: {run_dir}")
    return path.as_posix()
