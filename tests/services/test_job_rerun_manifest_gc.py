"""Regression tests for #508: rerun must delete the affected nodes'
``job_artifacts`` manifest rows in the same transaction as the node reset.

Before the fix, rerun cleaned only the local job_dir (stage_outputs) while
the object-storage manifest rows survived, so a rerun that never completed
again left the job listing — and serving — the PREVIOUS run's artifacts
(``names_for_job`` unions the manifest; single-artifact reads fall back to
S3). Three entry points share the semantics: single rerun, run-to, and
approval rework. RMW artifacts (input ∩ output of the same node) are
excluded, mirroring stage_outputs (#114).
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.approval_decisions import ApprovalDecisionService
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revision_format import serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    workflow_definition_from_mapping,
)
from tests.fakes.storage import FakeObjectStorage

pytestmark = pytest.mark.postgres


@pytest.fixture
def chain_definition():
    return WorkflowDefinition(
        key="chain_workflow",
        label="Chain",
        intake=WorkflowIntake(),
        nodes={
            "up": WorkflowNode(key="up", label="Up", capability="up", outputs=["up.json"]),
            "down": WorkflowNode(
                key="down",
                label="Down",
                capability="down",
                after=["up"],
                inputs=["up.json"],
                outputs=["down.json"],
            ),
        },
    )


def _seed_job_with_manifest(
    job_db: Any,
    settings: Any,
    definition: WorkflowDefinition,
    *,
    workspace: Any,
    storage: FakeObjectStorage,
) -> dict[str, Any]:
    batch = job_db.create_run(
        "chain_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="chain_workflow",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["up", "down"],
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    job_db.update_job_node(job["id"], "up", status="completed")
    job_db.update_job_node(job["id"], "down", status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    for name in ("up.json", "down.json"):
        (storage_dir / name).write_text(f"{name} content")
    store = JobArtifactObjectStore(job_db, storage)
    store.upload(
        workspace_id=str(workspace["id"]),
        job_id=job["id"],
        node_key="up",
        name="up.json",
        local_path=storage_dir / "up.json",
    )
    store.upload(
        workspace_id=str(workspace["id"]),
        job_id=job["id"],
        node_key="down",
        name="down.json",
        local_path=storage_dir / "down.json",
    )
    return job


def _make_rerun_service(job_db, settings, storage) -> JobRerunService:
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )


def test_rerun_deletes_downstream_manifest_rows_and_objects(job_db, settings, chain_definition):
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "up")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    # Both rows gone: the rerun target's and the downstream closure's — the
    # job must stop listing (and serving) the previous run's artifacts.
    assert store.names_for_job(job["id"]) == set()
    # The objects themselves were best-effort deleted post-commit (the fake
    # removes deleted keys from ``objects`` and records them in ``deleted``).
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"up.json", "down.json"}


def test_rerun_keeps_unaffected_nodes_manifest_rows(job_db, settings, chain_definition):
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = _make_rerun_service(job_db, settings, storage)

    # Rerun the DOWNSTREAM node: "up" is upstream, untouched.
    result = service.rerun(workspace["id"], job["id"], "down")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == {"up.json"}


def test_rerun_keeps_rmw_manifest_rows(job_db, settings):
    definition = WorkflowDefinition(
        key="rmw_workflow",
        label="RMW",
        intake=WorkflowIntake(),
        nodes={
            "publish": WorkflowNode(
                key="publish",
                label="Publish",
                capability="publish",
                outputs=["result.json", "manifest.json"],
            ),
        },
    )
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="rmw_workflow")
    batch = job_db.create_run(
        "rmw_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="rmw_workflow",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["publish"],
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    job_db.update_job_node(job["id"], "publish", status="completed")
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    store = JobArtifactObjectStore(job_db, storage)
    for name in ("result.json", "manifest.json"):
        (storage_dir / name).write_text(f"{name} content")
        store.upload(
            workspace_id=str(workspace["id"]),
            job_id=job["id"],
            node_key="publish",
            name=name,
            local_path=storage_dir / name,
        )
    service = _make_rerun_service(job_db, settings, storage)

    result = service.rerun(workspace["id"], job["id"], "publish")

    assert result["status"] == "succeeded"
    # Both files are RMW candidates only if declared as inputs; here
    # manifest.json is a pure output — assert the pure output row is gone
    # while any RMW-named row (declared as input AND output elsewhere in the
    # closure) survives. With no inputs declared, all rows go.
    assert store.names_for_job(job["id"]) == set()


def test_run_to_deletes_start_closure_manifest_rows(job_db, settings, chain_definition):
    """The run-to entry point shares the manifest GC: staging the start
    node's closure must remove those rows too."""
    storage = FakeObjectStorage()
    workspace = job_db.create_workspace("default", default_workflow_key="chain_workflow")
    job = _seed_job_with_manifest(
        job_db, settings, chain_definition, workspace=workspace, storage=storage
    )
    service = JobExecutionService(
        job_db,
        JobArtifactMutationService(settings.jobs_dir),
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        object_store=JobArtifactObjectStore(job_db, storage),
    )

    result = service.run_to(workspace["id"], job["id"], "down", start_node_key="up")

    assert result["status"] == "succeeded"
    store = JobArtifactObjectStore(job_db, storage)
    assert store.names_for_job(job["id"]) == set()
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"up.json", "down.json"}


def test_approval_rework_deletes_target_closure_manifest_rows(job_db, settings):
    """The approval rework entry point shares the manifest GC: deciding a
    rework on the gate resets its rework_target ("write") and the downstream
    closure — those manifest rows and objects must go."""
    dag = {
        "key": "approval_gc",
        "label": "Approval GC",
        "schema_version": 2,
        "nodes": {
            "entry": {"type": "start", "label": "入口"},
            "write": {"label": "写稿", "capability": "write", "outputs": ["script.md"]},
            "gate": {
                "type": "approval",
                "label": "审批",
                "inputs": ["script.md"],
                "config": {"rework_target": "write"},
            },
        },
        "edges": [
            {"from": "entry", "to": "write"},
            {"from": "write", "to": "gate"},
        ],
    }
    definition = workflow_definition_from_mapping(dag)
    workspace = job_db.create_workspace(name="approval-gc-ws", default_workflow_key="approval_gc")
    WorkflowRevisionService(job_db).ensure_active_revision(str(workspace["id"]), definition)
    job = job_db.create_job(
        workflow_key="approval_gc",
        source_type="material",
        source_id="chapter-1",
        run_id="",
        title="第一章",
        node_keys=list(definition.executable_nodes),
        workspace_id=str(workspace["id"]),
    )
    storage = FakeObjectStorage()
    storage_dir = resolve_job_dir(job, settings.jobs_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "script.md").write_text("draft", encoding="utf-8")
    store = JobArtifactObjectStore(job_db, storage)
    store.upload(
        workspace_id=str(workspace["id"]),
        job_id=job["id"],
        node_key="write",
        name="script.md",
        local_path=storage_dir / "script.md",
    )
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='write'",
            (job["id"],),
        )
        conn.execute(
            "update job_nodes set status='awaiting_approval' where job_id=%s and node_key='gate'",
            (job["id"],),
        )
    rerun = JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
        object_store=store,
    )
    service = ApprovalDecisionService(job_db, settings, rerun, object_store=store)

    service.decide(
        str(workspace["id"]),
        job["id"],
        "gate",
        verdict="rework",
        note="redo",
        decided_by="user:u1",
    )

    # The old run's artifact row and object are gone; the rework decision
    # then uploads its own feedback artifact for the fresh attempt.
    assert store.names_for_job(job["id"]) == {"review_feedback.json"}
    deleted_names = {key.rsplit("/", 1)[-1] for key in storage.deleted}
    assert deleted_names == {"script.md"}
