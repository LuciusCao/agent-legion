from server.app.workflows.schema import WorkflowDefinitionError

RETIRED_WORKFLOW_EXECUTION_GUIDANCE = (
    "Workflow top-level execution is retired (#805). For every Agent node, declare "
    "nodes.<agent_key>.execution.runtime plus its provider/model/thinking values; "
    "preserve each node's existing non-empty overrides and do not re-add a top-level "
    "execution block. Stored revisions and frozen jobs remain readable through the "
    "legacy compatibility path."
)


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


class RetiredWorkflowExecutionError(InvalidOperationError, WorkflowDefinitionError):
    """New authoring contains the retired top-level execution block (#805)."""
