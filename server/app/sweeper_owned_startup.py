"""Slow-cadence sweep-thread startup for the app composition root.

Split from ``worker_startup.py`` (#354, file-size budget): that module keeps
the execution-plane worker threads (sweeper + workflow worker), this one
owns the sweeper-replica-only slow sweeps.
"""

from __future__ import annotations

from server.app.jobs import JobQueries
from server.app.services.artifact_orphan_gc import ArtifactOrphanGcThread
from server.app.services.artifact_store import ArtifactStore
from server.app.services.execution_retention_sweeper import ExecutionRetentionThread
from server.app.services.job_artifact_maintenance import JobArtifactMaintenanceThread
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.material_ttl_sweeper import MaterialTtlSweeperThread
from server.app.settings import Settings
from server.app.storage import ObjectStorage


def start_sweeper_owned_threads(
    artifact_store: ArtifactStore,
    job_artifact_objects: JobArtifactObjectStore,
    job_db: JobQueries,
    settings: Settings,
    object_storage: ObjectStorage | None,
) -> tuple[
    ArtifactOrphanGcThread,
    JobArtifactMaintenanceThread,
    MaterialTtlSweeperThread,
    ExecutionRetentionThread,
]:
    """Start the four slow-cadence sweep threads the sweeper replica owns.

    Orphan GC / artifact maintenance / materials TTL (design §10) /
    execution-plane row retention (#354) share the sweeper ownership rule:
    exactly one replica (``sweeper_enabled``) runs them, the rest stay idle.
    """
    artifact_gc_thread = ArtifactOrphanGcThread(artifact_store)
    artifact_gc_thread.start()
    artifact_maintenance_thread = JobArtifactMaintenanceThread(
        job_artifact_objects, job_db, settings
    )
    artifact_maintenance_thread.start()
    material_ttl_thread = MaterialTtlSweeperThread(job_db, object_storage)
    material_ttl_thread.start()
    # Execution-plane row retention (#354): disabled (deletes nothing) until
    # the instance setting is explicitly enabled.
    execution_retention_thread = ExecutionRetentionThread(job_db)
    execution_retention_thread.start()
    return (
        artifact_gc_thread,
        artifact_maintenance_thread,
        material_ttl_thread,
        execution_retention_thread,
    )
