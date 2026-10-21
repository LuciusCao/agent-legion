from __future__ import annotations

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.groups import (
    IdentityQueriesMixin,
    RunDomainQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
)
from server.app.jobs.queries.job_batch_operations import (  # #946
    JobBatchOperationQueriesMixin,
)
from server.app.jobs.queries.material_claim_gate import MaterialClaimGateQueriesMixin  # #1279
from server.app.jobs.queries.material_repair import MaterialRepairQueriesMixin  # #1279
from server.app.jobs.queries.run_batches import RunBatchQueriesMixin  # #1117
from server.app.jobs.queries.run_batches_listing import RunBatchListingQueriesMixin  # #1157
from server.app.jobs.queries.run_healing import RunHealingQueriesMixin  # #501
from server.app.jobs.queries.token_usage_capture import TokenUsageCaptureQueriesMixin  # #1239
from server.app.jobs.queries.workflow_draft_generation import WorkflowDraftGenerationQueriesMixin
from server.app.jobs.queries.workflow_runtime_config import WorkflowRuntimeConfigQueriesMixin


class JobQueries(
    IdentityQueriesMixin,
    StudioChatDomainQueriesMixin,
    WorkspaceDomainQueriesMixin,
    RunDomainQueriesMixin,
    RunHealingQueriesMixin,
    WorkflowRevisionDomainQueriesMixin,
    # #1226: draft generation (the WorkspaceDomain group sits at its budget band).
    WorkflowDraftGenerationQueriesMixin,
    # M47: runtime config version history (the WorkflowRevisionDomain group sits
    # at its budget band).
    WorkflowRuntimeConfigQueriesMixin,
    # #1239: lease-finish token capture (the RunDomain group sits at its budget band).
    TokenUsageCaptureQueriesMixin,
    # #1279: material ETag repair/rebind (the RunDomain group sits at its
    # budget band).
    MaterialRepairQueriesMixin,
    # #1279 codex r7: material ETag protocol claim gate (same band).
    MaterialClaimGateQueriesMixin,
    # #1117: runs-async batch state row (standalone mixin; the domain groups
    # sit at their budget bands).
    RunBatchQueriesMixin,
    # #1157 phase3: task-center listing/archive views (standalone mixin).
    RunBatchListingQueriesMixin,
    # #946: batch-ops async task rows (same standalone-mixin argument).
    JobBatchOperationQueriesMixin,
    JobQueriesBase,
):
    """Backward-compatible facade for all workspace/job query operations.

    Composed from five domain groups (issue #195); see ``queries.groups``.
    """
