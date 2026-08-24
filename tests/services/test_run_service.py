"""RunService: item-based run creation (materials-and-runs design §4, slice 3)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from server.app.db.connection import connect_database
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


def _definition_accepting(*item_types: str):
    """Demo DAG with a widened start-node entry contract (default: material only)."""
    import copy

    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = list(item_types)
    return workflow_definition_from_dict(raw)


def _workspace_with_revision(
    job_db, settings, workspace_id: str = WORKSPACE_ID, definition=None
) -> dict:
    workspace = job_db.create_workspace(workspace_id, default_workflow_key=WORKFLOW_KEY)
    seed_demo_workspace_node_codes(settings, workspace["id"])
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], definition or load_builtin_definition(WORKFLOW_KEY)
    )
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


@pytest.fixture
def service_all_types(job_db, settings) -> RunService:
    """Workspace whose start node accepts both item types (default: material only)."""
    _workspace_with_revision(job_db, settings, definition=_definition_accepting("material", "ref"))
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


def test_ref_item_creates_job_with_verbatim_input(service_all_types, job_db) -> None:
    _insert_connection(job_db, "cms-main")
    item = _ref_item("cms-main", "Q-42", params={"lang": "zh"})

    result = service_all_types.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[item])

    job = result["jobs"][0]
    assert job["source_type"] == "ref"
    # Ref identity is connection-scoped: source_id carries the connection key.
    assert job["source_id"] == "cms-main:Q-42"
    assert job["title"] == "Q-42"
    stored = _fetch_job(job_db, job["id"])
    assert json.loads(stored["input_json"]) == item


def test_ref_identity_includes_connection_key(service_all_types, job_db) -> None:
    _insert_connection(job_db, "cms-a")
    _insert_connection(job_db, "cms-b")

    # The same external_id reachable through two connections is two items.
    result = service_all_types.create_run(
        WORKSPACE_ID,
        workflow_key=WORKFLOW_KEY,
        items=[_ref_item("cms-a", "Q-1"), _ref_item("cms-b", "Q-1")],
    )

    assert result["created_count"] == 2
    assert {job["source_id"] for job in result["jobs"]} == {"cms-a:Q-1", "cms-b:Q-1"}


def test_ref_dedup_is_scoped_per_connection(service_all_types, job_db) -> None:
    _insert_connection(job_db, "cms-a")
    _insert_connection(job_db, "cms-b")
    first = service_all_types.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_ref_item("cms-a", "Q-1")]
    )
    assert first["created_count"] == 1

    # Same external_id via another connection is fresh, not a duplicate.
    second = service_all_types.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_ref_item("cms-b", "Q-1")]
    )
    assert second["created_count"] == 1

    # The identical (connection_key, external_id) pair dedups like any item.
    with pytest.raises(InvalidOperationError, match="No tasks were resolved"):
        service_all_types.create_run(
            WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_ref_item("cms-a", "Q-1")]
        )


def test_mixed_items_create_one_job_each(service_all_types, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    _insert_connection(job_db, "cms-main")

    result = service_all_types.create_run(
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


def test_unknown_connection_key_is_rejected(service_all_types) -> None:
    with pytest.raises(InvalidOperationError, match="Unknown connection key"):
        service_all_types.create_run(
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


def test_validation_failures_leave_no_run_behind(service_all_types, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")
    with pytest.raises(InvalidOperationError, match="Unknown connection key"):
        service_all_types.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[_material_item("mat-1"), _ref_item("missing-conn", "Q-1")],
        )

    assert service_all_types.list_runs(WORKSPACE_ID) == []
    assert job_db.list_job_dedup_keys(WORKSPACE_ID, WORKFLOW_KEY) == set()


def test_intra_request_job_id_collision_leaves_no_run(service, job_db) -> None:
    # ``col/a`` and ``col_a`` normalize to the same job id (``_job_id`` maps
    # "/" to "_"); the collision surfaces only inside create_jobs_bulk, after
    # the run row already committed.
    _insert_material(job_db, WORKSPACE_ID, "col/a")
    _insert_material(job_db, WORKSPACE_ID, "col_a")

    with pytest.raises(InvalidOperationError, match="Job identity collision"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[_material_item("col/a"), _material_item("col_a")],
        )

    assert service.list_runs(WORKSPACE_ID) == []
    assert job_db.list_job_dedup_keys(WORKSPACE_ID, WORKFLOW_KEY) == set()


def test_cross_request_job_id_collision_compensates_the_new_run(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "col/a")
    _insert_material(job_db, WORKSPACE_ID, "col_a")
    first = service.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("col/a")]
    )
    assert first["created_count"] == 1

    # Different dedup key but the same job id as the existing row:
    # create_jobs_bulk fails inside its transaction and the fresh run row
    # must be compensated without touching the earlier run.
    with pytest.raises(InvalidOperationError, match="Job identity collision"):
        service.create_run(WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("col_a")])

    assert [run["id"] for run in service.list_runs(WORKSPACE_ID)] == [first["run"]["id"]]
    assert job_db.list_job_dedup_keys(WORKSPACE_ID, WORKFLOW_KEY) == {("material", "col/a")}


def test_material_deleted_mid_creation_fails_and_compensates_run(service, job_db) -> None:
    """TOCTOU 串行化（delete 先持锁方向）：材料删除已删行但未提交时，
    create_run 的无锁候选校验仍看到旧行，而 create_jobs_bulk 的 FOR KEY
    SHARE 阻塞到删除提交、行已消失 → InvalidOperationError（400），先行
    提交的 run 行由既有补偿逻辑删除，不留孤儿。"""
    _insert_material(job_db, WORKSPACE_ID, "mat-gone")

    outcome: list[str] = []
    entered = threading.Event()

    def _create() -> None:
        entered.set()
        try:
            service.create_run(
                WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-gone")]
            )
            outcome.append("created")
        except InvalidOperationError:
            outcome.append("invalid")
        except Exception as exc:  # 线程内意外失败也要带回主线程定位
            outcome.append(f"error:{exc!r}")

    holder = connect_database(job_db.path)
    try:
        with holder:
            # 模拟进行中的材料删除：行已删但事务未提交。
            holder.execute("delete from materials where id=%s", ("mat-gone",))
            thread = threading.Thread(target=_create)
            thread.start()
            assert entered.wait(timeout=5)
            time.sleep(0.5)  # create_run 线程应正阻塞在 FOR KEY SHARE 上
            assert thread.is_alive()
        # holder 提交删除，释放行锁
        thread.join(timeout=15)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert outcome == ["invalid"]
    assert service.list_runs(WORKSPACE_ID) == []
    assert job_db.list_job_dedup_keys(WORKSPACE_ID, WORKFLOW_KEY) == set()


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


def test_ref_item_rejected_under_material_only_contract(service, job_db) -> None:
    """D4: the demo start node accepts only materials; a ref item is rejected
    before any write (no run row, no dedup keys)."""
    _insert_connection(job_db, "cms-main")

    with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
        service.create_run(
            WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_ref_item("cms-main", "Q-1")]
        )

    assert service.list_runs(WORKSPACE_ID) == []
    assert job_db.list_job_dedup_keys(WORKSPACE_ID, WORKFLOW_KEY) == set()


def test_unsupported_item_type_rejected_by_entry_contract(service) -> None:
    """Unknown item types hit the entry contract before type dispatch."""
    with pytest.raises(InvalidOperationError, match="not accepted by this workflow"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=[{"type": "folder", "material_ids": ["m1"]}],
        )


def test_non_object_item_reports_shape_error_first(service) -> None:
    """A non-dict item must surface the shape error (aligned with
    ``_resolve_items``), not a misleading "type None not accepted"."""
    with pytest.raises(InvalidOperationError, match="Each item must be an object"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKFLOW_KEY,
            items=["not-an-object"],  # type: ignore[list-item]
        )


def test_material_item_accepted_under_material_only_contract(service, job_db) -> None:
    _insert_material(job_db, WORKSPACE_ID, "mat-1")

    result = service.create_run(
        WORKSPACE_ID, workflow_key=WORKFLOW_KEY, items=[_material_item("mat-1")]
    )

    assert result["created_count"] == 1
    job = result["jobs"][0]
    # The start node never enters job_nodes (EXEC-WORKFLOW-START-001).
    with job_db.connect() as conn:
        rows = conn.execute(
            "select node_key from job_nodes where job_id=%s order by node_key", (job["id"],)
        ).fetchall()
    assert "_start" not in [row["node_key"] for row in rows]
