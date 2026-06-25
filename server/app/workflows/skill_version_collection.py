from __future__ import annotations

from typing import Any


def collect_skill_versions(
    job_id: str,
    context: dict[str, Any] | None,
) -> dict[str, str]:
    """Return a mapping of node_key -> skill_version for a job.

    Reads ``node_runs`` from the database in *context* (key ``job_db``).
    When the same node has multiple runs, the later run wins.
    """
    if context is None:
        return {}
    job_db = context.get("job_db")
    if job_db is None:
        return {}

    versions: dict[str, str] = {}
    try:
        runs = job_db.list_node_runs(job_id)
    except Exception:
        return {}

    for run in runs:
        node_key = run.get("node_key")
        skill_version = run.get("skill_version") or ""
        if node_key and skill_version:
            versions[node_key] = skill_version
    return versions
