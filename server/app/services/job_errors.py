class JobServiceError(Exception):
    pass


class NotFoundError(JobServiceError):
    pass


class InvalidOperationError(JobServiceError):
    pass


class AllItemsAlreadyResolvedError(InvalidOperationError):
    """Every item in a submission already has a job and no failed run is
    healable — the submission as a whole is a no-op. The campaign feeder
    (#532 PR-C) catches THIS subclass to advance its cursor: the broad
    InvalidOperationError family also covers state-drift failures
    (expired materials, disabled connections, vanished revisions) that
    must fail the campaign instead of being silently absorbed as skips."""


class ConflictError(JobServiceError):
    pass


class UnsupportedOperationError(JobServiceError):
    pass


class CustomNodesDisabledError(JobServiceError):
    """Custom workflow node codes are disabled by configuration (routes map to 403)."""


class DraftWorkflowKeyMismatchError(InvalidOperationError):
    """Draft workflow key does not match the workspace default key (routes map to 422)."""
