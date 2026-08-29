from __future__ import annotations

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.groups import (
    IdentityQueriesMixin,
    RunDomainQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
)


class JobQueries(
    IdentityQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
    RunDomainQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    JobQueriesBase,
):
    """Backward-compatible facade for all workspace/job query operations.

    Composed from five domain groups (issue #195); see ``queries.groups``.
    """
