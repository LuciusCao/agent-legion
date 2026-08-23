from __future__ import annotations

from server.app.jobs.atomic_mutations import AtomicJobMutationsMixin
from server.app.jobs.execution_control import JobExecutionControlMixin
from server.app.jobs.queries.auth import AuthQueriesMixin
from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.batch import RunQueriesMixin
from server.app.jobs.queries.failed_node_runs import FailedNodeRunQueriesMixin
from server.app.jobs.queries.job_bulk import JobBulkQueriesMixin
from server.app.jobs.queries.job_keys import JobKeyQueriesMixin
from server.app.jobs.queries.job_nodes import JobNodeQueriesMixin
from server.app.jobs.queries.job_rerun_states import JobRerunStateQueriesMixin
from server.app.jobs.queries.job_scan_delta import JobScanDeltaMixin
from server.app.jobs.queries.job_scan_marks import JobScanMarksMixin
from server.app.jobs.queries.scoped_token_management import (
    ScopedTokenManagementQueriesMixin,
)
from server.app.jobs.queries.scoped_tokens import ScopedTokenQueriesMixin
from server.app.jobs.queries.status import JobStatusQueriesMixin
from server.app.jobs.queries.studio_chat import StudioChatQueriesMixin
from server.app.jobs.queries.workflow_revisions import WorkflowRevisionQueriesMixin
from server.app.jobs.queries.workspace import WorkspaceQueriesMixin
from server.app.jobs.queries.workspace_packages import WorkspacePackageQueriesMixin


class JobQueries(
    AuthQueriesMixin,
    ScopedTokenQueriesMixin,
    ScopedTokenManagementQueriesMixin,
    StudioChatQueriesMixin,
    WorkspacePackageQueriesMixin,
    WorkspaceQueriesMixin,
    RunQueriesMixin,
    WorkflowRevisionQueriesMixin,
    JobBulkQueriesMixin,
    JobNodeQueriesMixin,
    JobRerunStateQueriesMixin,
    FailedNodeRunQueriesMixin,
    JobScanDeltaMixin,
    JobScanMarksMixin,
    JobStatusQueriesMixin,
    JobKeyQueriesMixin,
    AtomicJobMutationsMixin,
    JobExecutionControlMixin,
    JobQueriesBase,
):
    """Backward-compatible facade for all workspace/job query operations."""
