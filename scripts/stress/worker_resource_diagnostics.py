"""Bounded, sanitized Host-code failure diagnostics for measurement artifacts."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.stress.worker_resource_host_code import _READ_LOG, _bounded
from scripts.stress.worker_resource_measurement import sanitize_log_text
from server.app.db.transaction import read_connection

_DATA_ROOT = PurePosixPath("/var/lib/agent-legion")


def _log_path(row: dict[str, Any]) -> str:
    raw = PurePosixPath(row["log_path"])
    path = raw.relative_to(_DATA_ROOT) if raw.is_absolute() else raw
    parts = path.parts
    if len(parts) != 6 or parts[:3] != ("logs", "jobs", "by-job") or ".." in parts:
        raise ValueError(f"rejected Host code log path: {path}")
    if parts[4] != row["job_id"] or parts[5] != f"{row['node_key']}.log":
        raise ValueError(f"Host code log ownership mismatch: {path}")
    run_dir = PurePosixPath(row.get("run_dir") or "")
    run_dir = run_dir.relative_to(_DATA_ROOT) if run_dir.is_absolute() else run_dir
    owned = run_dir.parts[:1] == ("jobs",) and {row["job_id"], row["node_key"]} <= set(
        run_dir.parts
    )
    if str(run_dir) not in {"", "."} and (".." in run_dir.parts or not owned):
        raise ValueError(f"Host code run_dir ownership mismatch: {run_dir}")
    return path.as_posix()


def write_host_code_diagnostics(run: Any, iteration_dir: Path) -> None:
    with read_connection(run.dsn) as conn:
        rows = conn.execute(
            "select id, job_id, node_key, status, error_message, log_path, run_dir"
            " from node_runs where job_id like %s order by id",
            (f"%measurement-{iteration_dir.name}-%",),
        ).fetchall()
    metadata, metadata_bytes = [], 2
    chunks, remaining = [], 1 << 20
    for row in rows:
        path = _log_path(row)
        item = {
            key: _bounded(row[key], 2048)
            for key in [
                "id",
                "job_id",
                "node_key",
                "status",
                "error_message",
                "log_path",
                "run_dir",
            ]
        }
        item_json = _bounded(json.dumps(item, default=str), 8192)
        if metadata_bytes + len(item_json.encode("utf-8")) <= 256 * 1024:
            metadata.append(item_json)
            metadata_bytes += len(item_json.encode("utf-8")) + 1
        if remaining <= 0:
            continue
        raw = run.compose(
            "exec", "-T", "host", "python3", "-c", _READ_LOG, path, str(_DATA_ROOT), capture=True
        )
        text = sanitize_log_text(raw, run.secrets)
        chunk = _bounded(f"\n== node_run {row['id']} {row['job_id']} ==\n{text}", remaining)
        chunks.append(chunk)
        remaining -= len(chunk.encode("utf-8"))
    runs_json = "[" + ",".join(metadata) + "]" + ("\n" if metadata_bytes < 256 * 1024 else "")
    (iteration_dir / "host-code-runs.json").write_text(runs_json, encoding="utf-8")
    suffix = "\n" if remaining > 0 else ""
    (iteration_dir / "host-code.log").write_text("".join(chunks) + suffix, encoding="utf-8")


def write_host_code_diagnostics_safely(run: Any, iteration_dir: Path) -> None:
    try:
        write_host_code_diagnostics(run, iteration_dir)
    except Exception as exc:
        # Diagnostic isolation: preserve the workload error and cleanup while exposing the failure.
        (iteration_dir / "host-code-diagnostics-error.txt").write_text(
            _bounded(sanitize_log_text(f"{type(exc).__name__}: {exc}", run.secrets), 65536) + "\n",
            encoding="utf-8",
        )
