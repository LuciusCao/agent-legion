from __future__ import annotations

from server.app.jobs.atomic_mutations import AtomicJobMutationsMixin
from server.app.jobs.execution_control import JobExecutionControlMixin
from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.batch import BatchQueriesMixin
from server.app.jobs.queries.job_nodes import JobNodeQueriesMixin
from server.app.jobs.queries.status import JobStatusQueriesMixin
from server.app.jobs.queries.workspace import WorkspaceQueriesMixin
from server.app.jobs.queries.workspace_packages import WorkspacePackageQueriesMixin


class JobQueries(
    WorkspacePackageQueriesMixin,
    WorkspaceQueriesMixin,
    BatchQueriesMixin,
    JobNodeQueriesMixin,
    JobStatusQueriesMixin,
    AtomicJobMutationsMixin,
    JobExecutionControlMixin,
    JobQueriesBase,
):
    """Backward-compatible facade for all workspace/job query operations."""
