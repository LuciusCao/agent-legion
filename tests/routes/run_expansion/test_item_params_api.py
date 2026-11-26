"""HTTP contract for generic material/bundle item params (#1565)."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from server.app.routes.run_item_contracts import RunItemRef, RunItemText
from server.app.services.run_batch_feeder import RunBatchFeeder
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
from server.app.workflows.definition import workflow_definition_from_dict
from tests.helpers import publish_legacy_intake_revision

WORKSPACE_ID = "run-item-params-api"
UNICODE_SECRET_KEYS = ("ｐａｓｓｗｏｒｄ", "ｓｅｃｒｅｔ", "api＿key", "𝐬𝐞𝐜𝐫𝐞𝐭")


def _workspace(client) -> str:
    response = client.post("/api/workspaces", json={"id": WORKSPACE_ID, "name": WORKSPACE_ID})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, WORKSPACE_ID)
    return WORKSPACE_ID


def _insert_material(client, material_id: str = "mat-params") -> str:
    with client.app.state.job_db.connect() as conn:
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


def _insert_bundle(client, bundle_id: str = "bundle-params") -> str:
    material_id = _insert_material(client, f"{bundle_id}-member")
    with client.app.state.job_db.connect() as conn:
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


def _accept_bundle_items(client) -> None:
    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "bundle"]
    WorkflowRevisionService(client.app.state.job_db).publish_workspace_revision(
        WORKSPACE_ID, workflow_definition_from_dict(raw)
    )


def _submit(client, items: list[dict], mode: str = "sync"):
    return client.post(
        f"/api/workspaces/{WORKSPACE_ID}/runs",
        json={"items": items, "mode": mode},
    )


def _jobs(client) -> list[dict]:
    with client.app.state.job_db.read() as conn:
        rows = conn.execute(
            "select id, input_json from jobs where workspace_id=%s order by id",
            (WORKSPACE_ID,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_sync_api_freezes_explicit_params(client) -> None:
    _workspace(client)
    material_id = _insert_material(client)
    params = {"variant": "blue", "options": {"count": 2, "flags": [True, None]}}

    response = _submit(
        client,
        [{"type": "material", "material_id": material_id, "params": params}],
    )

    assert response.status_code == 200, response.text
    (job,) = _jobs(client)
    assert json.loads(job["input_json"]) == {
        "type": "material",
        "material_id": material_id,
        "params": params,
    }


def test_async_api_freezes_items_at_accept_and_feeds_the_same_params(client) -> None:
    _workspace(client)
    material_id = _insert_material(client)
    params = {"variant": "async", "count": 3}

    response = _submit(
        client,
        [{"type": "material", "material_id": material_id, "params": params}],
        mode="async",
    )

    assert response.status_code == 202, response.text
    run_id = response.json()["run"]["id"]
    batch = client.app.state.job_db.get_run_batch(run_id)
    target_spec = json.loads(batch["target_spec_json"])
    assert target_spec["items"] == [
        {"type": "material", "material_id": material_id, "params": params}
    ]
    feeder = RunBatchFeeder(client.app.state.job_db, settings=client.app.state.settings)
    while feeder.consume_once():
        pass
    (job,) = _jobs(client)
    assert json.loads(job["input_json"])["params"] == params
    detail = client.get(f"/api/workspaces/{WORKSPACE_ID}/runs/{run_id}")
    assert detail.json()["batch"]["chunk_errors"] == []


@pytest.mark.parametrize("kind", ["material", "bundle"])
@pytest.mark.parametrize("secret_key", UNICODE_SECRET_KEYS)
def test_api_rejects_nested_unicode_secret_keys_without_writes(client, kind, secret_key) -> None:
    _workspace(client)
    entity_id = _insert_material(client) if kind == "material" else "bundle-params"

    response = _submit(
        client,
        [{"type": kind, f"{kind}_id": entity_id, "params": {"outer": {secret_key: "plaintext"}}}],
    )

    assert response.status_code == 422, response.text
    assert client.get(f"/api/workspaces/{WORKSPACE_ID}/runs").json()["runs"] == []
    assert _jobs(client) == []


@pytest.mark.parametrize("kind", ["material", "bundle"])
def test_api_freezes_unicode_business_keys_verbatim(client, kind) -> None:
    _workspace(client)
    _accept_bundle_items(client)
    entity_id = _insert_material(client) if kind == "material" else _insert_bundle(client)
    params = {"业务🌐": {"ｋｅｙ": "值", "clé": 1}, "variant": "blue"}

    response = _submit(
        client,
        [{"type": kind, f"{kind}_id": entity_id, "params": params}],
    )

    assert response.status_code == 200, response.text
    (job,) = _jobs(client)
    assert json.loads(job["input_json"]) == {
        "type": kind,
        f"{kind}_id": entity_id,
        "params": params,
    }


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "params",
    [
        {"secret_ref": "node:wf:node:field"},
        {"nested": {"password": "plaintext"}},
        {"x": "x" * 2049},
        {"x": [1] * 65},
        None,
    ],
)
def test_invalid_params_are_422_before_any_run_write(client, params, mode) -> None:
    _workspace(client)
    material_id = _insert_material(client)

    response = _submit(
        client,
        [{"type": "material", "material_id": material_id, "params": params}],
        mode=mode,
    )

    assert response.status_code == 422, response.text
    assert client.get(f"/api/workspaces/{WORKSPACE_ID}/runs").json()["runs"] == []
    assert _jobs(client) == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_same_submission_token_param_drift_is_400_without_run(client, mode) -> None:
    _workspace(client)
    material_id = _insert_material(client)
    items = [
        {
            "type": "material",
            "material_id": material_id,
            "client_token": "same",
            "params": {"variant": "a"},
        },
        {
            "type": "material",
            "material_id": material_id,
            "client_token": "same",
            "params": {"variant": "b"},
        },
    ]

    response = _submit(client, items, mode=mode)

    assert response.status_code == 400, response.text
    assert "different params" in response.json()["detail"]
    assert client.get(f"/api/workspaces/{WORKSPACE_ID}/runs").json()["runs"] == []
    assert _jobs(client) == []


def test_cross_request_token_param_drift_is_400_and_preserves_original_input(client) -> None:
    _workspace(client)
    material_id = _insert_material(client)
    first = {
        "type": "material",
        "material_id": material_id,
        "client_token": "same",
        "params": {"variant": "a"},
    }
    assert _submit(client, [first]).status_code == 200

    response = _submit(client, [{**first, "params": {"variant": "b"}}])

    assert response.status_code == 400, response.text
    assert "different params" in response.json()["detail"]
    assert len(client.get(f"/api/workspaces/{WORKSPACE_ID}/runs").json()["runs"]) == 1
    (job,) = _jobs(client)
    assert json.loads(job["input_json"])["params"] == {"variant": "a"}


@pytest.mark.no_db
def test_ref_and_text_contracts_stay_compatible() -> None:
    legacy_ref_params = {"secret_ref": "legacy-ref-value", "deep": {"anything": [1, 2]}}
    ref = RunItemRef.model_validate(
        {
            "type": "ref",
            "connection_key": "legacy",
            "external_id": "item-1",
            "params": legacy_ref_params,
        }
    )
    assert ref.params == legacy_ref_params
    with pytest.raises(ValidationError):
        RunItemText.model_validate({"type": "text", "content": "hello", "params": {}})
