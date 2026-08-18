"""Regression tests for #114: a node declaring the same artifact as both input
and output (read-modify-write) must keep that artifact through rerun staging;
otherwise the rerun node waits forever on an input no producer rewrites."""

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revision_format import serialize_definition
from server.app.storage_paths import make_data_relative, resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from server.app.workflows.scheduler import find_ready_nodes


@pytest.fixture
def rmw_definition():
    return WorkflowDefinition(
        key="rmw_workflow",
        label="RMW Workflow",
        intake=WorkflowIntake(),
        nodes={
            "produce": WorkflowNode(
                key="produce",
                label="Produce",
                capability="produce",
                outputs=["data.json", "manifest.json"],
            ),
            "publish": WorkflowNode(
                key="publish",
                label="Publish",
                capability="publish",
                after=["produce"],
                inputs=["data.json", "manifest.json"],
                outputs=["publish_result.json", "manifest.json"],
            ),
        },
    )


def test_stage_outputs_keeps_read_modify_write_artifacts(tmp_path, rmw_definition):
    jobs_dir = tmp_path / "jobs"
    storage_dir = jobs_dir / "job"
    storage_dir.mkdir(parents=True)
    (storage_dir / "data.json").write_text("data")
    (storage_dir / "manifest.json").write_text("manifest")
    (storage_dir / "publish_result.json").write_text("result")

    job = {"storage_dir": make_data_relative(storage_dir, tmp_path)}
    service = JobArtifactMutationService(jobs_dir)
    staged = service.stage_outputs(job, ["publish"], rmw_definition)

    # Pure outputs are staged away; the read-modify-write artifact stays.
    assert not (storage_dir / "publish_result.json").exists()
    assert (storage_dir / "manifest.json").read_text() == "manifest"
    assert (storage_dir / "data.json").exists()

    staged.commit()
    assert (storage_dir / "manifest.json").read_text() == "manifest"


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db.path, data_dir=settings.data_dir),
        settings,
        WorkflowCatalogService(settings),
        JobArtifactMutationService(settings.jobs_dir),
    )


def test_rerun_rmw_node_keeps_artifact_and_stays_reschedulable(
    job_db, settings, rerun_service, rmw_definition
):
    workspace = job_db.create_workspace("default", default_workflow_key="rmw_workflow")
    batch = job_db.create_batch(
        "rmw_workflow",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="rmw_workflow",
        source_type="question",
        source_id="Q1",
        batch_id=batch["id"],
        title="Question 1",
        node_keys=["produce", "publish"],
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=serialize_definition(rmw_definition),
    )
    job_db.update_job_node(job["id"], "produce", status="completed")
    job_db.update_job_node(job["id"], "publish", status="completed")

    storage = resolve_job_dir(job, settings.jobs_dir)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "data.json").write_text("data")
    (storage / "manifest.json").write_text("manifest")
    (storage / "publish_result.json").write_text("result")

    result = rerun_service.rerun(workspace["id"], job["id"], "publish")

    assert result["status"] == "succeeded"
    # The read-modify-write artifact stays; the pure output is removed.
    assert (storage / "manifest.json").read_text() == "manifest"
    assert not (storage / "publish_result.json").exists()

    # The node is immediately schedulable again instead of pending forever
    # on an input no rerun producer will rewrite (#114).
    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert statuses["publish"] == "pending"
    ready = find_ready_nodes(rmw_definition, statuses, storage)
    assert [node.key for node in ready] == ["publish"]
