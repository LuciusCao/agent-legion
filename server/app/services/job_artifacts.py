from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError


class JobArtifactService:
    def __init__(self, job_db: JobQueries):
        self.job_db = job_db

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _artifact_path(self, job: dict[str, Any], artifact_name: str) -> Path:
        if "/" in artifact_name or "\\" in artifact_name or artifact_name in {"", ".", ".."}:
            raise InvalidOperationError("Invalid artifact name")

        base = Path(str(job["storage_dir"])).resolve()
        path = (base / artifact_name).resolve()
        if path.parent != base:
            raise InvalidOperationError("Invalid artifact path")
        return path

    def read(self, job_id: str, artifact_name: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        if not path.exists() or not path.is_file():
            raise NotFoundError("Artifact not found")
        return {"name": artifact_name, "content": path.read_text(encoding="utf-8")}

    def reject_subpath(self, job_id: str) -> None:
        self._job_or_404(job_id)
        raise InvalidOperationError("Invalid job path")
