"""Generic material/bundle item params: validation, freeze and idempotency (#1565)."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.run_item_params import (
    RUN_ITEM_PARAMS_MAX_ARRAY_ITEMS,
    RUN_ITEM_PARAMS_MAX_KEY_CHARS,
    RUN_ITEM_PARAMS_MAX_KEYS,
    RUN_ITEM_PARAMS_MAX_STRING_BYTES,
    validate_item_params,
)
from server.app.services.run_payload import reconstruct_batch_payload
from server.app.services.run_service import RunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
from server.app.workflows.definition import workflow_definition_from_dict
from tests.helpers import publish_builtin_revision

pytest_plugins = ("tests.services.shared_resources.conftest",)

WORKSPACE_ID = "education_video_problems_generation"
PARAMS = {
    "variant": "blue",
    "options": {"count": 2, "enabled": True, "labels": ["a", "b"], "empty": None},
}


@pytest.fixture
def service(job_db, settings) -> RunService:
    job_db.create_workspace(WORKSPACE_ID)
    publish_builtin_revision(job_db, WORKSPACE_ID)
    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "bundle"]
    WorkflowRevisionService(job_db).publish_workspace_revision(
        WORKSPACE_ID, workflow_definition_from_dict(raw)
    )
    return RunService(job_db, settings)


def _insert_material(job_db, material_id: str = "mat-params") -> str:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                WORKSPACE_ID,
                f"hash-{material_id}",
                f"{WORKSPACE_ID}/hash-{material_id}/doc.txt",
            ),
        )
    return material_id


def _insert_bundle(job_db, bundle_id: str = "bundle-params") -> str:
    material_id = _insert_material(job_db, f"{bundle_id}-member")
    with job_db.connect() as conn:
        conn.execute(
            "insert into material_bundles(id, workspace_id, name, total_size_bytes,"
            " file_count, created_by) values (%s, %s, 'folder', 10, 1, 'tester')",
            (bundle_id, WORKSPACE_ID),
        )
        conn.execute(
            "insert into material_bundle_members(bundle_id, material_id, path, ordinal)"
            " values (%s, %s, 'doc.txt', 0)",
            (bundle_id, material_id),
        )
    return bundle_id


def _item(kind: str, entity_id: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, f"{kind}_id": entity_id, **extra}


def _drain(service: RunService) -> None:
    feeder = RunBatchFeeder(service.job_db, settings=service.settings)
    while feeder.consume_once():
        pass


def _jobs(job_db) -> list[dict[str, Any]]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, run_id, source_type, source_id, input_json from jobs"
            " where workspace_id=%s order by id",
            (WORKSPACE_ID,),
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize("kind", ["material", "bundle"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_params_freeze_into_job_input_and_sdk_payload(service, job_db, kind, mode) -> None:
    entity_id = _insert_material(job_db) if kind == "material" else _insert_bundle(job_db)
    item = _item(kind, entity_id, params=copy.deepcopy(PARAMS), client_token=f"{kind}-{mode}")

    if mode == "sync":
        result = service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[item])
        assert result["created_count"] == 1
        run_id = result["run"]["id"]
    else:
        accepted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[item])
        run_id = accepted["run"]["id"]
        _drain(service)
        detail = service.get_run(WORKSPACE_ID, run_id)
        assert detail["batch"]["status"] == "completed"
        assert detail["batch"]["chunk_errors"] == []

    (job,) = _jobs(job_db)
    expected = _item(kind, entity_id, params=PARAMS)
    assert json.loads(job["input_json"]) == expected
    run = job_db.get_run(run_id)
    payload = reconstruct_batch_payload(run, job)
    assert payload["task_candidates"] == [expected]


def test_omitted_params_keep_legacy_input_shape(service, job_db) -> None:
    material_id = _insert_material(job_db)

    result = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKSPACE_ID,
        items=[_item("material", material_id)],
    )

    assert result["created_count"] == 1
    (job,) = _jobs(job_db)
    assert json.loads(job["input_json"]) == {"type": "material", "material_id": material_id}


def test_same_token_same_params_dedups_but_different_params_reject(service, job_db) -> None:
    material_id = _insert_material(job_db)
    first = _item(
        "material", material_id, client_token="variant-1", params={"variant": "a", "count": 1}
    )
    service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[first])

    same_params_different_key_order = _item(
        "material", material_id, client_token="variant-1", params={"count": 1, "variant": "a"}
    )
    with pytest.raises(InvalidOperationError, match="No tasks were resolved"):
        service.create_run(
            WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[same_params_different_key_order]
        )

    with pytest.raises(InvalidOperationError, match="different params"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKSPACE_ID,
            items=[
                _item("material", material_id, client_token="variant-1", params={"variant": "b"})
            ],
        )
    assert len(service.list_runs(WORKSPACE_ID)) == 1
    (job,) = _jobs(job_db)
    assert json.loads(job["input_json"])["params"] == {"variant": "a", "count": 1}


def test_write_side_race_with_different_params_rejects_and_cleans_new_run(
    service, job_db, monkeypatch
) -> None:
    material_id = _insert_material(job_db)
    first = _item("material", material_id, client_token="same", params={"variant": "a"})
    service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[first])

    monkeypatch.setattr(
        service.job_db,
        "filter_existing_dedup_keys",
        lambda *args, **kwargs: set(),
    )
    monkeypatch.setattr(
        service.job_db,
        "filter_existing_dedup_inputs",
        lambda *args, **kwargs: [],
    )
    conflicting = _item("material", material_id, client_token="same", params={"variant": "b"})
    with pytest.raises(InvalidOperationError, match="different params"):
        service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[conflicting])

    assert len(service.list_runs(WORKSPACE_ID)) == 1
    (job,) = _jobs(job_db)
    assert json.loads(job["input_json"])["params"] == {"variant": "a"}


@pytest.mark.no_db
def test_shared_resource_dedup_uses_the_actual_pins_job_namespace() -> None:
    from server.app.services.job_creation_pipeline import JobCreationPipeline

    workspace_id = "ws-shared-params"
    source_id = "mat-shared~same"
    pins_hash = "pins-hash"

    class SharedDedupDb:
        def filter_existing_dedup_inputs(self, workspace, keys, pins):
            assert (workspace, pins) == (workspace_id, pins_hash)
            assert keys == [("material", source_id)]
            return [
                {
                    "source_type": "material",
                    "source_id": source_id,
                    "input_json": json.dumps(
                        {"type": "material", "material_id": "mat-shared", "params": {"v": "old"}}
                    ),
                }
            ]

    pipeline = JobCreationPipeline(SharedDedupDb(), settings=object())
    candidate = {
        "entity_type": "material",
        "entity_id": source_id,
        "title": "doc",
        "stem": "",
        "input": {"type": "material", "material_id": "mat-shared", "params": {"v": "new"}},
    }
    with pytest.raises(InvalidOperationError, match="different params"):
        pipeline.collect_fresh_candidates(workspace_id, [candidate], pins_hash)

    candidate["input"]["params"] = {"v": "old"}
    assert pipeline.collect_fresh_candidates(workspace_id, [candidate], pins_hash) == []


def test_shared_pins_transition_and_conflict_cleanup(
    job_db, settings, shared_service, shared_materials, shared_storage
) -> None:
    from server.app.workflows.definition import workflow_definition_from_mapping
    from tests.services.shared_resources.conftest import SOURCE_WS, TARGET_WS
    from tests.services.shared_resources.helpers import create_resource, ready_material

    resource_material = ready_material(
        shared_materials, shared_storage, SOURCE_WS, b"shared-params", "rules.txt"
    )
    resource = create_resource(shared_service, SOURCE_WS, "material", resource_material)
    resource_id = str(resource["id"])
    shared_service.share(SOURCE_WS, resource_id, target_workspace_id=TARGET_WS, actor_id="owner")
    material_id = ready_material(shared_materials, shared_storage, TARGET_WS, b"item", "item.txt")
    service = RunService(job_db, settings, materials_service=shared_materials)

    def publish(resource_ids: list[str]) -> None:
        definition = workflow_definition_from_mapping(
            {
                "key": TARGET_WS,
                "label": "Target",
                "nodes": {
                    "draft": {
                        "type": "code",
                        "capability": "draft",
                        "shared_resources": resource_ids,
                    }
                },
            }
        )
        WorkflowRevisionService(job_db).publish_workspace_revision(TARGET_WS, definition)

    item = _item("material", material_id, client_token="same", params={"v": "ordinary"})
    publish([])
    ordinary = service.create_run(TARGET_WS, workflow_key=TARGET_WS, items=[item])

    publish([resource_id])
    shared = service.create_run(
        TARGET_WS,
        workflow_key=TARGET_WS,
        items=[{**item, "params": {"v": "shared"}}],
    )
    assert shared["created_count"] == 1
    assert shared["job_ids"][0] != ordinary["job_ids"][0]

    before_run_ids = {ordinary["run"]["id"], shared["run"]["id"]}
    with pytest.raises(InvalidOperationError, match="different params"):
        service.create_run(
            TARGET_WS,
            workflow_key=TARGET_WS,
            items=[{**item, "params": {"v": "changed"}}],
        )
    assert {run["id"] for run in service.list_runs(TARGET_WS)} == before_run_ids
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, input_json from jobs where workspace_id=%s",
            (TARGET_WS,),
        ).fetchall()
    assert len(rows) == 2
    inputs = {str(row["id"]): json.loads(row["input_json"]) for row in rows}
    assert inputs[ordinary["job_ids"][0]]["params"] == {"v": "ordinary"}
    assert inputs[shared["job_ids"][0]]["params"] == {"v": "shared"}

    publish([])
    with pytest.raises(InvalidOperationError, match="different params"):
        service.create_run(
            TARGET_WS,
            workflow_key=TARGET_WS,
            items=[{**item, "params": {"v": "ordinary"}}],
        )
    assert {run["id"] for run in service.list_runs(TARGET_WS)} == before_run_ids
    with job_db.read() as conn:
        count = conn.execute(
            "select count(*) as n from jobs where workspace_id=%s", (TARGET_WS,)
        ).fetchone()
    assert int(count["n"]) == 2


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_same_submission_token_param_drift_fails_before_run_write(service, mode) -> None:
    material_id = _insert_material(service.job_db)
    items = [
        _item("material", material_id, client_token="same", params={"variant": "a"}),
        _item("material", material_id, client_token="same", params={"variant": "b"}),
    ]

    with pytest.raises(InvalidOperationError, match="different params"):
        if mode == "sync":
            service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)
        else:
            service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=items)

    assert service.list_runs(WORKSPACE_ID) == []
    assert _jobs(service.job_db) == []


def test_tokenless_dedup_and_distinct_tokens_keep_existing_identity_rules(service, job_db) -> None:
    material_id = _insert_material(job_db)
    plain = _item("material", material_id, params={"variant": "first"})
    service.create_run(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[plain])

    with pytest.raises(InvalidOperationError, match="No tasks were resolved"):
        service.create_run(
            WORKSPACE_ID,
            workflow_key=WORKSPACE_ID,
            items=[_item("material", material_id, params={"variant": "second"})],
        )

    result = service.create_run(
        WORKSPACE_ID,
        workflow_key=WORKSPACE_ID,
        items=[
            _item("material", material_id, client_token="a", params={"variant": "a"}),
            _item("material", material_id, client_token="b", params={"variant": "b"}),
        ],
    )
    assert result["created_count"] == 2
    jobs = _jobs(job_db)
    assert len(jobs) == 3
    assert sorted(json.loads(job["input_json"])["params"]["variant"] for job in jobs) == [
        "a",
        "b",
        "first",
    ]


def test_async_cross_request_token_param_drift_becomes_chunk_error(service, job_db) -> None:
    material_id = _insert_material(job_db)
    first = _item("material", material_id, client_token="same", params={"variant": "a"})
    accepted = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[first])
    _drain(service)
    assert service.get_run(WORKSPACE_ID, accepted["run"]["id"])["batch"]["chunk_errors"] == []

    conflicting = _item("material", material_id, client_token="same", params={"variant": "b"})
    retry = service.create_run_async(WORKSPACE_ID, workflow_key=WORKSPACE_ID, items=[conflicting])
    _drain(service)

    detail = service.get_run(WORKSPACE_ID, retry["run"]["id"])
    assert detail["batch"]["status"] == "completed"
    assert "different params" in detail["batch"]["error_message"]
    assert detail["batch"]["jobs_created"] == 0
    (job,) = _jobs(job_db)
    assert json.loads(job["input_json"])["params"] == {"variant": "a"}


@pytest.mark.no_db
@pytest.mark.parametrize(
    "params",
    [
        {"secret_ref": "node:wf:node:field"},
        {"nested": {"secret_set": True}},
        {"secret_key": "plaintext"},
        {"api-key": "plaintext"},
        {"accessKey": "plaintext"},
        {"sessionToken": "plaintext"},
        {"password": "plaintext"},
        {"pwd": "plaintext"},
        {"authToken": "plaintext"},
        {"clientSecret": "plaintext"},
        {"credentials": {"value": "plaintext"}},
    ],
)
def test_secret_shapes_are_rejected_at_any_depth(params) -> None:
    with pytest.raises(InvalidOperationError, match="non-secret"):
        validate_item_params(params)


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ([], "must be an object"),
        ({"a": {"b": {"c": {"d": 1}}}}, "maximum depth"),
        ({f"k{index}": index for index in range(RUN_ITEM_PARAMS_MAX_KEYS + 1)}, "key count"),
        ({"x" * (RUN_ITEM_PARAMS_MAX_KEY_CHARS + 1): 1}, "at most"),
        ({"x": "x" * (RUN_ITEM_PARAMS_MAX_STRING_BYTES + 1)}, "UTF-8 bytes"),
        ({"x": list(range(RUN_ITEM_PARAMS_MAX_ARRAY_ITEMS + 1))}, "at most"),
        ({"x": math.nan}, "finite"),
        ({"x": math.inf}, "finite"),
        ({"x": {1, 2}}, "JSON values"),
        ({"x": b"bytes"}, "JSON values"),
        ({f"k{index}": "x" * 1000 for index in range(17)}, "canonical JSON UTF-8 bytes"),
        ({"": 1}, "non-empty strings"),
    ],
)
def test_params_bounds_and_json_types(params, detail) -> None:
    with pytest.raises(InvalidOperationError, match=detail):
        validate_item_params(params)


@pytest.mark.no_db
def test_valid_params_are_returned_without_normalization() -> None:
    params = copy.deepcopy(PARAMS)
    assert validate_item_params(params) is params
    assert params == PARAMS
