"""Campaign service wiring for the router tree (#532 PR-A).

The router factory stays at its budget; this module builds the
CampaignService with its own JobRerunService instance (a stateless wrapper
over the job_db/lease singletons — the same shape the PR-B feeder will
construct in create_app, design §3.1). Event wiring passes through so rerun
preview and the eventual feeder share the singleton event buffer.
"""

from __future__ import annotations

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.routes.deps import RouterDeps
from server.app.services.campaign_service import CampaignService
from server.app.services.job_rerun import JobRerunService


def build_campaign_service(deps: RouterDeps) -> CampaignService:
    return CampaignService(
        deps.job_db,
        deps.settings,
        rerun_service=JobRerunService(
            deps.job_db,
            ExecutorLeaseRepository(
                deps.job_db,
                data_dir=deps.settings.data_dir,
                job_event_manager=deps.job_event_manager,
                job_event_buffer=deps.job_event_buffer,
            ),
            deps.settings,
            job_event_manager=deps.job_event_manager,
            job_event_buffer=deps.job_event_buffer,
        ),
        object_storage=deps.job_artifact_objects,
    )
