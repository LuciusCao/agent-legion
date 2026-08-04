from __future__ import annotations

from pathlib import Path


def build_job_dir_index(jobs_dir: Path, job_ids: set[str]) -> dict[str, Path]:
    """Map job_id to its workspace-qualified job directory.

    Scans ``jobs_dir`` once so backfill does not re-scan every workspace for
    every node_run row. Only directories whose name is in ``job_ids`` are
    indexed, avoiding noise such as ``.git`` or ``prompts`` inside video
    workspaces.
    """
    index: dict[str, Path] = {}
    if not jobs_dir.is_dir() or not job_ids:
        return index
    for workspace_dir in jobs_dir.iterdir():
        if not workspace_dir.is_dir():
            continue
        for candidate in workspace_dir.iterdir():
            if candidate.is_dir() and candidate.name in job_ids:
                index[candidate.name] = candidate
    return index


def derive_run_dir_from_index(
    job_id: str, node_key: str, job_dir_index: dict[str, Path]
) -> Path | None:
    """Return the newest token run directory using a pre-built job_dir index."""
    if not job_id or not node_key:
        return None
    job_dir = job_dir_index.get(job_id)
    if job_dir is None:
        return None
    run_parent = job_dir / "runs" / node_key
    if not run_parent.is_dir():
        return None
    token_dirs = [d for d in run_parent.iterdir() if d.is_dir()]
    if not token_dirs:
        return None
    return max(token_dirs, key=lambda p: p.stat().st_mtime)
