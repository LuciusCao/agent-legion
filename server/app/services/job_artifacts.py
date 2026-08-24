from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.storage_paths import resolve_job_dir


class JobArtifactService:
    def __init__(
        self,
        job_db: JobQueries,
        object_store: JobArtifactObjectStore | None = None,
    ) -> None:
        self.job_db = job_db
        # D12 read path: object storage first, local job_dir fallback (legacy
        # jobs were never uploaded; an unconfigured instance has no rows).
        self.object_store = object_store

    def _job_or_404(self, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _artifact_path(self, job: dict[str, Any], artifact_name: str) -> Path:
        if "/" in artifact_name or "\\" in artifact_name or artifact_name in {"", ".", ".."}:
            raise InvalidOperationError("Invalid artifact name")

        base = resolve_job_dir(job, self.job_db.jobs_dir)
        path = (base / artifact_name).resolve()
        if path.parent != base:
            raise InvalidOperationError("Invalid artifact path")
        return path

    def _read_object(self, job_id: str, artifact_name: str) -> dict[str, Any] | None:
        if self.object_store is None or not self.object_store.enabled:
            return None
        row = self.object_store.lookup(job_id, artifact_name)
        if row is None:
            return None
        stream = self.object_store.open_stream(row)
        content = stream.read().decode("utf-8")
        return {"name": artifact_name, "content": content}

    def read(self, job_id: str, artifact_name: str) -> dict[str, Any]:
        job = self._job_or_404(job_id)
        path = self._artifact_path(job, artifact_name)
        if path.exists() and path.is_file():
            return {"name": artifact_name, "content": path.read_text(encoding="utf-8")}
        stored = self._read_object(job_id, artifact_name)
        if stored is not None:
            return stored
        raise NotFoundError("Artifact not found")

    def reject_subpath(self, job_id: str) -> None:
        self._job_or_404(job_id)
        raise InvalidOperationError("Invalid job path")
