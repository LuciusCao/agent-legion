from __future__ import annotations

from server.app.services.job_errors import ConflictError, InvalidOperationError, JobServiceError


class SharedResourceStorageUnavailableError(JobServiceError):
    pass


class SharedResourceCopyError(InvalidOperationError):
    def __init__(self, code: str, path: str) -> None:
        self.code = code
        self.file_path = path
        super().__init__(f"Shared resource copy failed ({code}) for file {path!r}")


class SharedResourceSelectionConflictError(ConflictError):
    def __init__(self, current_revision: int) -> None:
        self.payload = {
            "message": "Shared resource selection revision conflict",
            "current_revision": current_revision,
        }
        super().__init__(self.payload["message"])


class SharedResourceInUseError(ConflictError):
    def __init__(self, selection_references: int, job_pin_references: int) -> None:
        self.payload = {
            "message": "Shared resource version still has references",
            "selection_references": selection_references,
            "job_pin_references": job_pin_references,
        }
        super().__init__(self.payload["message"])
