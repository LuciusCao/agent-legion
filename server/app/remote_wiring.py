"""Composition wiring for the remote execution completion path."""

from __future__ import annotations

from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.executors.remote_completion import RemoteCompletionHandler
from server.app.services.artifact_store import ArtifactStore


def register_remote_completion(
    broker: RemoteExecutionBroker,
    leases: ExecutorLeaseRepository,
    jobs_dir: Path,
    artifact_store: ArtifactStore | None,
) -> None:
    """Register the lease-finishing callback on any process owning a broker."""
    broker.register_completion_callback(
        RemoteCompletionHandler(
            broker,
            leases,
            jobs_dir,
            artifact_store=artifact_store,
        ).handle_completion
    )
