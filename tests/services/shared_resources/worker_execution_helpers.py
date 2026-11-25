from __future__ import annotations

from server.app.services.node_codes import NodeCodeService
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.services.shared_resources.conftest import SOURCE_WS, TARGET_WS
from tests.services.shared_resources.helpers import create_resource, ready_material


def create_worker_resource_job(
    job_db,
    settings,
    shared_service,
    shared_materials,
    shared_storage,
    *,
    code_text: str = "def run(job, job_dir, runtime):\n    pass\n",
):
    material_id = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"resource-v1", "rules.txt"
    )
    resource = create_resource(shared_service, SOURCE_WS, "material", material_id)
    resource_id = str(resource["id"])
    shared_service.share(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")
    item_id = ready_material(shared_materials, shared_storage, TARGET_WS, b"item", "item.txt")
    codes = NodeCodeService(job_db.dsn_identity)
    codes.save_draft(TARGET_WS, TARGET_WS, "prepare", code_text, "test seed")
    codes.publish(TARGET_WS, TARGET_WS, "prepare")
    definition = workflow_definition_from_mapping(
        {
            "key": TARGET_WS,
            "label": "Target",
            "nodes": {
                "prepare": {
                    "type": "code",
                    "capability": "prepare",
                    "shared_resources": [resource_id],
                    "outputs": ["shared_resources.json"],
                }
            },
        }
    )
    WorkflowRevisionService(job_db).ensure_active_revision(TARGET_WS, definition)
    created = RunService(job_db, settings, materials_service=shared_materials).create_run(
        TARGET_WS,
        workflow_key=TARGET_WS,
        items=[{"type": "material", "material_id": item_id}],
    )
    job = job_db.get_job(created["job_ids"][0])
    manifest = {"node_key": "prepare", "shared_resources": [resource_id]}
    return resource_id, job, manifest
