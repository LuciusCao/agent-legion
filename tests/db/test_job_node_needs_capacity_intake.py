from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_creation_pipeline import JobCreationPipeline
from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.node_code_seeding import seed_workspace_node_code
from server.app.services.node_codes import NodeCodeService
from server.app.services.quality_replay_setup import QualityReplaySetup
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.capacity_metadata import node_capacity_requirements
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def _definition(key: str, gate_type: str) -> WorkflowDefinition:
    approval = gate_type == "approval"
    return WorkflowDefinition(
        key=key,
        label=key,
        intake=WorkflowIntake(),
        nodes={
            "run": WorkflowNode(key="run", label="run", capability="run"),
            "gate": WorkflowNode(
                key="gate",
                label="gate",
                capability="" if approval else "gate",
                node_type=gate_type,
            ),
        },
        edges=[WorkflowEdge(source="run", target="gate")],
    )


def _revision(definition: WorkflowDefinition) -> dict[str, Any]:
    snapshot = serialize_definition(definition)
    return {
        "id": "rev-intake",
        "version": 1,
        "definition_hash": definition_hash(snapshot),
        "definition_json": snapshot,
    }


def _capacity_rows(job_db: JobQueries, job_id: str) -> dict[str, bool]:
    return {
        str(row["node_key"]): bool(row["needs_capacity"]) for row in job_db.list_job_nodes(job_id)
    }


def test_single_and_unified_bulk_intake_write_needs_capacity(job_db: JobQueries, settings) -> None:
    definition = _definition("intake", "approval")
    job_db.create_workspace("Intake", workspace_id=definition.key)
    single = job_db.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="single",
        run_id="",
        title="single",
        node_keys=list(definition.executable_nodes),
        workspace_id=definition.key,
        needs_capacity_by_node=node_capacity_requirements(definition),
    )
    assert _capacity_rows(job_db, str(single["id"])) == {"run": True, "gate": False}

    pipeline = JobCreationPipeline(job_db, settings)
    bulk_ids = pipeline.insert_jobs(
        candidates=[{"entity_id": "bulk", "entity_type": "question", "title": "bulk"}],
        workflow_key=definition.key,
        run_id="",
        definition=definition,
        workspace_id=definition.key,
        revision=_revision(definition),
        node_config={},
    )
    assert len(bulk_ids) == 1
    assert _capacity_rows(job_db, bulk_ids[0]) == {"run": True, "gate": False}


def test_intake_without_metadata_fails_closed(job_db: JobQueries) -> None:
    job_db.create_workspace("Legacy", workspace_id="legacy")
    job = job_db.create_job(
        workflow_key="legacy",
        source_type="question",
        source_id="legacy",
        run_id="",
        title="legacy",
        node_keys=["unknown"],
        workspace_id="legacy",
    )
    assert _capacity_rows(job_db, str(job["id"])) == {"unknown": True}


def test_quality_replay_uses_its_own_snapshot_metadata(job_db: JobQueries, tmp_path: Path) -> None:
    own = _definition("replay", "approval")
    active = _definition("replay", "code")
    job_db.create_workspace("Replay", workspace_id=own.key)
    codes = NodeCodeService(TEST_DATABASE_URL)
    for node in active.executable_nodes.values():
        seed_workspace_node_code(
            codes, own.key, own.key, node.key, "def run(ctx):\n    pass\n", "test"
        )
    WorkflowRevisionService(job_db).ensure_active_revision(own.key, active)
    snapshot = serialize_definition(own)
    original = job_db.create_job(
        workflow_key=own.key,
        source_type="question",
        source_id="original",
        run_id="",
        title="original",
        node_keys=list(own.executable_nodes),
        workspace_id=own.key,
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
        needs_capacity_by_node=node_capacity_requirements(own),
    )

    copy_id = QualityReplaySetup(job_db, None).build_copy_job(
        own.key,
        {"id": "item-1"},
        original,
        own,
        own.nodes["run"],
        "replay-1",
        None,
    )

    copy = job_db.get_job(copy_id)
    assert copy["workflow_definition_snapshot_json"] == snapshot
    assert _capacity_rows(job_db, copy_id) == {"run": True, "gate": False}


@pytest.mark.parametrize("mode", ("clean", "inherit"))
def test_workflow_upgrade_retypes_capacity_metadata(
    job_db: JobQueries, tmp_path: Path, mode: str
) -> None:
    workspace_id = f"upgrade-{mode}"
    old = _definition(workspace_id, "code")
    job_db.create_workspace("Upgrade", workspace_id=workspace_id)
    revisions = WorkflowRevisionService(job_db)
    codes = NodeCodeService(TEST_DATABASE_URL)
    for node in old.executable_nodes.values():
        seed_workspace_node_code(
            codes, workspace_id, workspace_id, node.key, "def run(ctx):\n    pass\n", "test"
        )
    original = revisions.publish_workspace_revision(workspace_id, old)
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type="question",
        source_id="retype",
        run_id="",
        title="retype",
        node_keys=list(old.executable_nodes),
        workspace_id=workspace_id,
        workflow_revision_id=str(original["id"]),
        workflow_version=int(original["version"]),
        workflow_definition_hash=str(original["definition_hash"]),
        workflow_definition_snapshot_json=str(original["definition_json"]),
        needs_capacity_by_node=node_capacity_requirements(old),
    )
    service = JobWorkflowUpgradeService(job_db, ExecutorLeaseRepository(job_db, data_dir=tmp_path))

    approval = _definition(workspace_id, "approval")
    revisions.publish_workspace_revision(workspace_id, approval)
    assert service.upgrade(workspace_id, str(job["id"]), mode=mode)["status"] == "succeeded"
    assert _capacity_rows(job_db, str(job["id"]))["gate"] is False

    code_again = _definition(workspace_id, "code")
    revisions.publish_workspace_revision(workspace_id, code_again)
    assert service.upgrade(workspace_id, str(job["id"]), mode=mode)["status"] == "succeeded"
    assert _capacity_rows(job_db, str(job["id"]))["gate"] is True
