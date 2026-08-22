"""RunService: item-based run creation (materials-and-runs design §4, slice 3)."""

from __future__ import annotations

import json

import pytest

from server.app.services.agent_service import published_agent_definitions
from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition

WORKFLOW_KEY = "education_video_problems_generation"
WORKSPACE_ID = "ws-run-service"
OTHER_WORKSPACE_ID = "ws-run-service-other"


def _workspace_with_revision(job_db, settings, workspace_id: str = WORKSPACE_ID) -> dict:
    workspace = job_db.create_workspace(workspace_id, default_workflow_key=WORKFLOW_KEY)
    definition = load_builtin_definition(WORKFLOW_KEY)
    seed_demo_workspace_node_codes(settings, workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    return workspace


def _insert_material(
    job_db,
    workspace_id: str,
    material_id: str,
    *,
    status: str = "ready",
    filename: str = "doc.txt",
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, %s, 'text/plain', 10, %s, %s, 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                filename,
                f"{workspace_id}/hash-{material_id}/{filename}",
                status,
            ),
        )


def _insert_connection(job_db, key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json)"
            " values (%s, 'hmac_token', %s, '{}')",
            (key, key),
        )


@pytest.fixture
def service(job_db, settings) -> RunService:
    _workspace_with_revision(job_db, settings)
    return RunService(job_db, settings)


def _material_item(material_id: str) -> dict:
    return {"type": "material", "material_id": material_id}


def _ref_item(connection_key: str, external_id: str, **extra) -> dict:
    return {"type": "ref", "connection_key": connection_key, "external_id": external_id, **extra}


def _fetch_job(job_db, job_id: str) -> dict:
    with job_db.connect() as conn:
        row = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
    assert row is not None
    return dict(row)


def test_material_item_creates_job_with_frozen_input(service, job_db, settings) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1", filename="notes.pdf")

    result = service.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-1")]
    )

    assert result["created_count"] == 1
    run = result["run"]
    assert run["source_kind"] == "items"
    assert run["workflow_key"] == WORKFLOW_KEY
    assert "node_code_versions" in run["frozen_pins"]
    job = result["jobs"][0]
    assert job["source_type"] == "material"
    assert job["source_id"] == "mat-1"
    assert job["title"] == "notes.pdf"
    assert job["batch_id"] == run["id"]
    stored = _fetch_job(job_db, job["id"])
    assert json.loads(stored["input_json"]) == {"type": "material", "material_id": "mat-1"}
    # The frozen config is exactly what the intake resolution chain produces.
    definition = load_builtin_definition(WORKFLOW_KEY)
    expected_config = resolve_workflow_node_configs(
        definition,
        published_agent_definitions(settings.database_url, WORKSPACE_ID),
        job_db.get_workspace(WORKSPACE_ID),
    )
    assert json.loads(stored["frozen_config_json"]) == expected_config


def test_ref_item_creates_job_with_verbatim_input(service, job_db) -> None:
    _insert_connection(job_db, "cms-main")
    item = _ref_item("cms-main", "Q-42", params={"lang": "zh"})

    result = service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[item])

    job = result["jobs"][0]
    assert job["source_type"] == "ref"
    assert job["source_id"] == "Q-42"
    assert job["title"] == "Q-42"
    stored = _fetch_job(job_db, job["id"])
    assert json.loads(stored["input_json"]) == item


def test_mixed_items_create_one_job_each(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    _insert_connection(job_db, "cms-main")

    result = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item("mat-1"), _ref_item("cms-main", "Q-1")],
    )

    assert result["created_count"] == 2
    assert {job["source_type"] for job in result["jobs"]} == {"material", "ref"}
    assert all(job["run_id"] == result["run"]["id"] for job in result["jobs"])


def test_material_not_ready_is_rejected(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-uploading", status="uploading")

    with pytest.raises(InvalidOperationError, match="not ready"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[_material_item("mat-uploading")],
        )


def test_cross_workspace_material_is_rejected(service, job_db, settings) -> None:
    _workspace_with_revision(job_db, settings, OTHER_WORKSPACE_ID)
    _insert_material(job_db, OTHER_WORKSPACE_ID, "mat-foreign")

    with pytest.raises(NotFoundError, match="Material not found"):
        service.create_run(
            WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-foreign")]
        )


def test_unknown_connection_key_is_rejected(service) -> None:
    with pytest.raises(InvalidOperationError, match="Unknown connection key"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[_ref_item("missing-conn", "Q-1")],
        )


def test_duplicate_items_are_filtered(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    _insert_material(job_db, WORKSPACE_ID, "mat-2")
    first = service.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-1")]
    )
    assert first["created_count"] == 1

    # A resubmission mixing an existing item with a fresh one keeps only the fresh.
    second = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item("mat-1"), _material_item("mat-2")],
    )
    assert second["created_count"] == 1
    assert second["jobs"][0]["source_id"] == "mat-2"

    # Nothing fresh left: the whole request is rejected like legacy intake.
    with pytest.raises(InvalidOperationError, match="No tasks were resolved"):
        service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-1")])


def test_intra_request_duplicates_create_one_job(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")

    result = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_material_item("mat-1"), _material_item("mat-1")],
    )

    assert result["created_count"] == 1


def test_validation_failures_leave_no_run_behind(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    with pytest.raises(InvalidOperationError, match="Unknown connection key"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[_material_item("mat-1"), _ref_item("missing-conn", "Q-1")],
        )

    assert service.list_runs(WORKSPACE_ID) == []
    assert job_db.list_job_dedup_keys(WORKSPACE_ID) == set()


def test_missing_workspace_and_revision_are_rejected(service) -> None:
    with pytest.raises(NotFoundError, match="Workspace not found"):
        service.create_run("missing", workflow_key=WORKFLOW_KEY, items=[_material_item("m")])
    with pytest.raises(InvalidOperationError, match="no active workflow revision"):
        service.create_run(
            WORKSPACE_ID, workflow_key="unknown_workflow", items=[_material_item("m")]
        )


def test_empty_items_are_rejected(service) -> None:
    with pytest.raises(InvalidOperationError, match="At least one item"):
        service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[])


def test_list_and_get_run(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    created = service.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-1")]
    )
    run_id = created["run"]["id"]

    runs = service.list_runs(WORKSPACE_ID)
    assert [run["id"] for run in runs] == [run_id]

    detail = service.get_run(WORKSPACE_ID, run_id)
    assert detail["run"]["status"] == "created"
    assert detail["run"]["created_count"] == 1
    assert detail["job_stats"] == {"total": 1, "by_status": {"queued": 1}}

    with pytest.raises(NotFoundError, match="Run not found"):
        service.get_run(OTHER_WORKSPACE_ID, run_id)
    with pytest.raises(NotFoundError, match="Run not found"):
        service.get_run(WORKSPACE_ID, "missing-run")
