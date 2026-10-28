"""Bounded, sanitized Host-code failure diagnostics for measurement artifacts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.stress.worker_resource_measurement import sanitize_log_text
from server.app.db.transaction import read_connection

_LOG_ROOT = PurePosixPath("/var/lib/agent-legion/logs/jobs/by-job")
_READ_LOG = (
    "import os,stat,sys; "
    "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW); "
    "st=os.fstat(fd); "
    "assert stat.S_ISREG(st.st_mode); "
    "sys.stdout.buffer.write(os.read(fd,65536)); "
    "os.close(fd)"
)


def _bounded(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _log_path(raw: object) -> str:
    path = PurePosixPath(str(raw))
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(_LOG_ROOT):
        raise ValueError(f"rejected non-measurement Host code log path: {path}")
    return str(path)


def write_host_code_diagnostics(run: Any, iteration_dir: Path) -> None:
    with read_connection(run.dsn) as conn:
        rows = conn.execute(
            "select id, job_id, node_key, status, error_message, command_json, log_path, run_dir"
            " from node_runs where node_key like 'resource_%' order by id"
        ).fetchall()
    metadata = []
    chunks = []
    remaining = 1 << 20
    for row in rows:
        path = _log_path(row["log_path"])
        command = _bounded(
            sanitize_log_text(json.dumps(row.get("command_json")), run.secrets), 4096
        )
        metadata.append({**row, "command_json": command, "log_path": path})
        if remaining <= 0:
            continue
        raw = run.compose("exec", "-T", "host", "python3", "-c", _READ_LOG, path, capture=True)
        text = _bounded(sanitize_log_text(raw, run.secrets), min(65536, remaining))
        chunk = f"\n== node_run {row['id']} {row['job_id']} ==\n{text}"
        chunks.append(chunk)
        remaining -= len(chunk.encode("utf-8"))
    (iteration_dir / "host-code-runs.json").write_text(
        sanitize_log_text(json.dumps(metadata, indent=2, default=str), run.secrets) + "\n",
        encoding="utf-8",
    )
    (iteration_dir / "host-code.log").write_text("".join(chunks) + "\n", encoding="utf-8")


def write_host_code_diagnostics_safely(run: Any, iteration_dir: Path) -> None:
    try:
        write_host_code_diagnostics(run, iteration_dir)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        (iteration_dir / "host-code-diagnostics-error.txt").write_text(
            sanitize_log_text(f"{type(exc).__name__}: {exc}", run.secrets) + "\n",
            encoding="utf-8",
        )
