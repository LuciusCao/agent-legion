from __future__ import annotations

from typing import Any

from server.app.workflows.skill_version_fallbacks import (
    UNAVAILABLE_SKILL_VERSION,
    configured_skill_fallbacks,
    job_node_fallbacks,
)


def collect_skill_versions(
    job_id: str,
    context: dict[str, Any] | None,
    job: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return node_key -> skill version, including fallback entries."""
    if context is None:
        return {}
    job_db = context.get("job_db")
    if job_db is None:
        return {}

    versions = job_node_fallbacks(job_id, job_db)
    versions.update(configured_skill_fallbacks(job, context))
    try:
        runs = job_db.list_node_runs(job_id)
    except Exception:
        return versions

    for run in runs:
        node_key = run.get("node_key")
        if node_key:
            versions[node_key] = run.get("skill_version") or versions.get(
                node_key, UNAVAILABLE_SKILL_VERSION
            )
    return versions
