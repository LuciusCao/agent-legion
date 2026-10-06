"""run_service 分块提交测试的共享夹具（#467；#955 拆分 test_run_service_chunking 时抽出）。"""

from __future__ import annotations

from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WORKFLOW_KEY = "education_video_problems_generation"
WORKSPACE_ID = "ws-run-chunk"


def definition_accepting_refs():
    import copy

    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "ref"]
    return workflow_definition_from_dict(raw)


def workspace(job_db, settings) -> None:
    job_db.create_workspace(WORKSPACE_ID)
    from server.app.services.demo_node_seed import seed_demo_workspace_node_codes

    seed_demo_workspace_node_codes(settings, WORKSPACE_ID)
    WorkflowRevisionService(job_db).ensure_active_revision(
        WORKSPACE_ID, load_builtin_definition(WORKFLOW_KEY)
    )


def insert_materials(job_db, count: int, prefix: str = "mat") -> None:
    with job_db.connect() as conn:
        for i in range(count):
            material_id = f"{prefix}-{i}"
            conn.execute(
                "insert into materials(id, workspace_id, content_hash, filename, content_type,"
                " size_bytes, storage_key, status, created_by)"
                " values (%s, %s, %s, %s, 'text/plain', 10, %s, 'ready', 'tester')"
                " on conflict (id) do nothing",
                (
                    material_id,
                    WORKSPACE_ID,
                    f"hash-{material_id}",
                    f"{material_id}.txt",
                    f"{WORKSPACE_ID}/hash-{material_id}/{material_id}.txt",
                ),
            )


def material_item(material_id: str) -> dict:
    return {"type": "material", "material_id": material_id}


def run_job_count(job_db, run_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute("select count(*) as n from jobs where run_id=%s", (run_id,)).fetchone()
    return int(row["n"])
