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
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_rerun import JobRerunService


def build_campaign_service(deps: RouterDeps) -> CampaignService:
    # deps.job_artifact_objects is the JobArtifactObjectStore wrapper; the
    # manifest spill path (PR-A P1) needs the ObjectStorage client under its
    # ``.storage`` seam (put_object), and None stays None — the 503
    # CampaignStorageUnavailableError branch for S3-less instances. Passing
    # the wrapper itself would AttributeError on the first oversized
    # manifest instead of spilling (or 503-ing).
    artifact_objects = deps.job_artifact_objects
    object_storage = (
        artifact_objects.storage
        if isinstance(artifact_objects, JobArtifactObjectStore)
        else artifact_objects
    )
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
        object_storage=object_storage,
    )
