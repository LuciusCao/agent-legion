"""Bounded, sanitized Host-code failure diagnostics for measurement artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.stress.worker_resource_host_code import _READ_LOG, _bounded
from scripts.stress.worker_resource_log_path import _DATA_ROOT
from scripts.stress.worker_resource_log_path import _log_path as _log_path
from scripts.stress.worker_resource_measurement import sanitize_log_text
from server.app.db.transaction import read_connection


def write_host_code_diagnostics(run: Any, iteration_dir: Path) -> None:
    with read_connection(run.dsn) as conn:
        rows = conn.execute(
            "select node_runs.id, node_runs.job_id, node_runs.node_key, node_runs.status,"
            " node_runs.error_message, node_runs.log_path, node_runs.run_dir, jobs.workspace_id"
            " from node_runs join jobs on jobs.id = node_runs.job_id"
            " where node_runs.job_id like %s order by node_runs.id",
            (f"%measurement-{iteration_dir.name}-%",),
        ).fetchall()
    metadata, metadata_bytes = [], 2
    chunks, remaining = [], 1 << 20
    for row in rows:
        path = _log_path(row)
        item = {
            key: _bounded(sanitize_log_text(str(row[key]), run.secrets), 2048)
            for key in (
                "id",
                "job_id",
                "node_key",
                "status",
                "error_message",
                "log_path",
                "run_dir",
            )
        }
        item_json = json.dumps(item)
        item_size = len(item_json.encode("utf-8"))
        if item_size <= 8192 and metadata_bytes + item_size <= 256 * 1024:
            metadata.append(item_json)
            metadata_bytes += item_size + 1
        if remaining <= 0:
            continue
        raw = run.compose(
            "exec", "-T", "host", "python3", "-c", _READ_LOG, path, str(_DATA_ROOT), capture=True
        )
        text = sanitize_log_text(
            f"\n== node_run {row['id']} {row['job_id']} ==\n{raw}", run.secrets
        )
        chunk = _bounded(text, remaining)
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
