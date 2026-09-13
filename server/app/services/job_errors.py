class JobServiceError(Exception):
    pass


class NotFoundError(JobServiceError):
    pass


class InvalidOperationError(JobServiceError):
    pass


class ConflictError(JobServiceError):
    pass


class DraftConflictError(ConflictError):
    """CAS draft save lost the race (#633); routes map to a 409 whose detail
    carries the conflict payload (message + expected/current updated_at +
    the current draft) so the agent can rebase without a second read."""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(str(payload["message"]))


class UnsupportedOperationError(JobServiceError):
    pass


class CustomNodesDisabledError(JobServiceError):
    """Custom workflow node codes are disabled by configuration (routes map to 403)."""


class DraftWorkflowKeyMismatchError(InvalidOperationError):
    """Draft workflow key does not match the workspace default key (routes map to 422)."""


class InvalidDraftCasTokenError(InvalidOperationError):
    """#633 codex review P2-2: expected_updated_at is neither the literal
    'never-saved' nor a parseable ISO timestamp (routes map to 422) — the
    timestamptz cast must never see it as a DB error (500)."""
