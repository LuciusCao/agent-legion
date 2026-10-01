"""Shared seeding/registration helpers for the external artifact-access
route tests (#631), split from test_external_artifacts.py when it crossed
the 800-line test-file budget (#779 codex train review P1-3). The sibling
test files import these; the ``two_workspaces`` fixture lives in the
directory conftest.
"""

from __future__ import annotations

import gzip
import hashlib

from server.app.services.job_artifact_objects import JobArtifactObjectStore


def _seed_workspace(c, workspace_id: str) -> None:
    from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions

    c.post("/api/workspaces", json={"id": workspace_id, "name": workspace_id})
    seed_workspace_agent_definitions(workspace_id)
    publish_legacy_intake_revision(c.app.state.job_db, workspace_id)


def _create_job(c, workspace_id: str, source_id: str = "Q003") -> dict:
    created = c.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": workspace_id,
            "source_kind": "direct_ids",
            "knowledge_point_ids": [source_id],
        },
    ).json()
    return created["jobs"][0]


def _register_object_artifact(
    c, job: dict, name: str, payload: bytes, *, node_key: str = "upstream", gzipped: bool = True
) -> None:
    """Register an authority-copy manifest row the way the Worker-direct
    channel does (HEAD-verified record_remote), storing the given bytes."""
    store: JobArtifactObjectStore = c.app.state.job_artifact_objects
    stored = gzip.compress(payload) if gzipped else payload
    storage_key = f"jobs/{job['workspace_id']}/{job['id']}/{name}"
    if gzipped:
        storage_key += ".gz"
    store.storage.objects[storage_key] = stored
    store.record_remote(
        workspace_id=job["workspace_id"],
        job_id=job["id"],
        node_key=node_key,
        name=name,
        storage_key=storage_key,
        size_bytes=len(stored),
        content_hash=hashlib.sha256(payload).hexdigest(),
    )


def _publish_subpath_revision(c, workspace_id: str) -> None:
    """Publish the demo variant whose publish node declares the nested output
    ``reports/final.json`` (the declared-subpath shape), then create a job —
    its intake-frozen snapshot carries the nested declaration."""
    import dataclasses

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_demo_legacy_intake_definition

    definition = load_demo_legacy_intake_definition()
    nodes = dict(definition.nodes)
    nodes["publish_content"] = dataclasses.replace(
        nodes["publish_content"], outputs=["reports/final.json"]
    )
    definition = dataclasses.replace(definition, key=workspace_id, nodes=nodes)
    WorkflowRevisionService(c.app.state.job_db).publish_workspace_revision(workspace_id, definition)
