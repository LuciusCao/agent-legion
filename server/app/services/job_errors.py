class JobServiceError(Exception):
    pass


class NotFoundError(JobServiceError):
    pass


class InvalidOperationError(JobServiceError):
    pass


class ConflictError(JobServiceError):
    pass


class UnsupportedOperationError(JobServiceError):
    pass


class CustomNodesDisabledError(JobServiceError):
    """Custom workflow node codes are disabled by configuration (routes map to 403)."""


class DraftWorkflowKeyMismatchError(InvalidOperationError):
    """Draft workflow key does not match the workspace default key (routes map to 422)."""
