from __future__ import annotations

from pathlib import Path


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
