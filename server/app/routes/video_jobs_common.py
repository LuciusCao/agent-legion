from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from server.app.jobs import JobQueries


def video_job_or_404(job_db: JobQueries, job_id: str) -> dict[str, Any]:
    job = job_db.get_job(job_id)
    if (
        job is None
        or job.get("source_type") != "video"
        or job.get("workflow_key") != "video_knowledge"
    ):
        raise HTTPException(status_code=404, detail="Video job not found")
    return job
