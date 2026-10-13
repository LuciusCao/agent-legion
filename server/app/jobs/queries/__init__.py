from __future__ import annotations

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.groups import (
    IdentityQueriesMixin,
    RunDomainQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
)
from server.app.jobs.queries.material_repair import MaterialRepairQueriesMixin  # #1279
from server.app.jobs.queries.run_healing import RunHealingQueriesMixin  # #501
from server.app.jobs.queries.token_usage_capture import TokenUsageCaptureQueriesMixin  # #1239
from server.app.jobs.queries.workflow_draft_generation import WorkflowDraftGenerationQueriesMixin


class JobQueries(
    IdentityQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
    RunDomainQueriesMixin,
    RunHealingQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    # #1226: draft generation (the WorkspaceDomain group sits at its budget band).
    WorkflowDraftGenerationQueriesMixin,
    # #1239: lease-finish token capture (the RunDomain group sits at its budget band).
    TokenUsageCaptureQueriesMixin,
    # #1279: material ETag repair/rebind (the RunDomain group sits at its
    # budget band).
    MaterialRepairQueriesMixin,
    JobQueriesBase,
):
    """Backward-compatible facade for all workspace/job query operations.

    Composed from five domain groups (issue #195); see ``queries.groups``.
    """
