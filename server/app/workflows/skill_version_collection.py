from __future__ import annotations

import logging
from typing import Any

from server.app.workflows.skill_version_fallbacks import configured_skill_fallbacks

logger = logging.getLogger(__name__)


def collect_skill_versions(
    job_id: str,
    context: dict[str, Any] | None,
    job: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return node_key -> skill version for skill-backed nodes only."""
    if context is None:
        return {}
    job_db = context.get("job_db")
    if job_db is None:
        return {}

    versions = configured_skill_fallbacks(job, context)
    try:
        runs = job_db.list_node_runs(job_id)
    except Exception:
        logger.debug("list_node_runs failed for job %s", job_id, exc_info=True)
        return versions

    for run in runs:
        node_key = run.get("node_key")
        skill_version = run.get("skill_version")
        if node_key and skill_version:
            versions[node_key] = skill_version
    return versions
