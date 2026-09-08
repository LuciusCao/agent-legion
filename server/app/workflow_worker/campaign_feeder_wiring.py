"""Campaign feeder wiring for the composition root (#532 PR-B).

build_campaign_feeder assembles the feeder's service set — its own
RunService / JobRerunService / JobWorkflowUpgradeService instances sharing
the job_db / job_event_buffer / lease singletons, so events and leases
behave exactly as the route tree's synchronous entry points' (the
routes/campaign_wiring precedent, design §3.1). main.py stays at its
budget: one construction line instead of thirty.
"""

from __future__ import annotations

from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.run_service import RunService
from server.app.settings import Settings
from server.app.workflow_worker.campaign_feeder import CampaignFeeder


def build_campaign_feeder(
    job_db: JobQueries,
    settings: Settings,
    executor_leases: ExecutorLeaseRepository,
    job_event_manager: Any,
    job_event_buffer: Any,
    workspace_worker_control: Any,
) -> CampaignFeeder:
    """Positional on purpose: the composition-root call stays one screen line
    pair (main.py sits at its budget ceiling)."""
    return CampaignFeeder(
        job_db,
        settings,
        rerun_service=JobRerunService(
            job_db,
            executor_leases,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        ),
        upgrade_service=JobWorkflowUpgradeService(
            job_db,
            executor_leases,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        ),
        run_service=RunService(
            job_db,
            settings,
            job_event_manager=job_event_manager,
            job_event_buffer=job_event_buffer,
        ),
        workspace_worker_control=workspace_worker_control,
    )
