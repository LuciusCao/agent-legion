"""Domain-grouped mixins composing the JobQueries facade (issue #195).

JobQueries previously declared 18 flat mixins; this module groups them into
five domain facades so the composition reads by domain. The linearization
order is adjusted by the grouping, but method resolution is unaffected in
practice: the mixins have zero attribute-name collisions and never use
``super()`` (non-cooperative inheritance), so every public method resolves
to the same function object as before the regrouping.
"""

from __future__ import annotations

from server.app.jobs.atomic_mutations import AtomicJobMutationsMixin
from server.app.jobs.execution_control import JobExecutionControlMixin
from server.app.jobs.queries.auth import AuthQueriesMixin
from server.app.jobs.queries.batch import RunQueriesMixin
from server.app.jobs.queries.connection import ConnectionQueriesMixin
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
from server.app.jobs.queries.workflow_drafts import WorkflowDraftQueriesMixin
from server.app.jobs.queries.workflow_revisions import WorkflowRevisionQueriesMixin
from server.app.jobs.queries.workspace import WorkspaceQueriesMixin
from server.app.jobs.queries.workspace_packages import WorkspacePackageQueriesMixin


class IdentityQueriesMixin(
    AuthQueriesMixin,
    ScopedTokenQueriesMixin,
    ScopedTokenManagementQueriesMixin,
):
    """User auth, scoped tokens: who may call what."""


class WorkspaceDomainQueriesMixin(
    WorkflowDraftQueriesMixin,
    WorkspacePackageQueriesMixin,
    WorkspaceQueriesMixin,
    ConnectionQueriesMixin,
):
    """Workspace lifecycle, packages, agent routes, drafts, and connections."""


class RunDomainQueriesMixin(
    RunQueriesMixin,
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
):
    """Runs, jobs, nodes, scans, reruns, and execution control."""


class StudioChatDomainQueriesMixin(StudioChatQueriesMixin):
    """Studio chat sessions and messages."""


class WorkflowRevisionDomainQueriesMixin(WorkflowRevisionQueriesMixin):
    """Workflow revision persistence."""
