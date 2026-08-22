"""Quality replay routes: auth, create/list/detail flow, replay labels (schema v29)."""

from __future__ import annotations

import pytest

from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema


@pytest.fixture
def client(client_factory):
    """Private app per test: these tests write frozen inputs into the app
    data_dir derived from the function-scoped tmp_path; the worker-session
    shared app has its own session data_dir, so the replay route would not
    find the files there."""
    with client_factory(fresh=True) as c:
        yield c


def _seed(client_tmp_path):
    """Seed a workspace with one completed job + sample item; returns ids."""
    job_db = JobQueries(TEST_DATABASE_URL, client_tmp_path / "jobs")
    ws = job_db.create_workspace(default_workflow_key="demo_workflow", name="Replay Routes WS")
    workspace_id = str(ws["id"])
    definition = WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "intake": WorkflowNode(
                key="intake",
                label="intake",
                capability="intake_items",
                outputs=["question.json"],
            ),
            "generate": WorkflowNode(
                key="generate",
                label="generate",
                capability="generate_key_info",
                after=["intake"],
                inputs=["question.json"],
                outputs=["key_info.json"],
            ),
        },
    )
    snapshot = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
        workflow_revision_id="rev-1",
        workflow_version=1,
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
    )
    job_id = str(job["id"])
    resolve_job_dir(job, job_db.jobs_dir).joinpath("question.json").write_text(
        '{"q": 1}', encoding="utf-8"
    )
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspace_node_routes("
            "workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (%s, 'test', 'generate', 'handler_executor', 'code-default')",
            (workspace_id,),
        )
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id=%s",
            (job_id,),
        )
        run = conn.execute(
            "insert into node_runs(job_id, node_key, status) values (%s, 'generate', 'completed')"
            " returning id",
            (job_id,),
        ).fetchone()
        conn.execute(
            "insert into quality_sample_batches(id, workspace_id, name, sample_size)"
            " values ('batch-1', %s, 'batch', 10)",
            (workspace_id,),
        )
        conn.execute(
            "insert into quality_sample_items(id, batch_id, node_run_id, job_id, node_key)"
            " values ('item-1', 'batch-1', %s, %s, 'generate')",
            (run["id"], job_id),
        )
    return workspace_id


def test_replay_create_list_detail_flow(client, tmp_path):
    workspace_id = _seed(tmp_path)
    base = f"/api/workspaces/{workspace_id}/quality"

    created = client.post(f"{base}/sample-items/item-1/replays", json={})
    assert created.status_code == 200, created.text
    replay = created.json()["replay"]
    assert replay["status"] == "pending"
    assert replay["agent_version"] is None
    assert f"replay-{replay['id']}" in replay["replay_job_id"]

    listing = client.get(f"{base}/sample-items/item-1/replays")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["replays"]] == [replay["id"]]

    detail = client.get(f"{base}/replays/{replay['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["replay"]["id"] == replay["id"]
    assert body["labels"] == []
    assert body["artifacts"] == []

    # A second replay while the first is pending conflicts.
    again = client.post(f"{base}/sample-items/item-1/replays", json={})
    assert again.status_code == 409


def test_replay_label_via_item_labels_endpoint(client, tmp_path):
    workspace_id = _seed(tmp_path)
    base = f"/api/workspaces/{workspace_id}/quality"
    replay = client.post(f"{base}/sample-items/item-1/replays", json={}).json()["replay"]

    labeled = client.post(
        f"{base}/sample-items/item-1/labels",
        json={"verdict": "bad", "reason_codes": ["fact_error"], "replay_id": replay["id"]},
    )
    assert labeled.status_code == 200, labeled.text
    label = labeled.json()["label"]
    assert label["target"] == "replay"
    assert label["replay_id"] == replay["id"]

    detail = client.get(f"{base}/replays/{replay['id']}").json()
    assert [row["verdict"] for row in detail["labels"]] == ["bad"]

    bogus = client.post(
        f"{base}/sample-items/item-1/labels",
        json={"verdict": "good", "replay_id": "missing"},
    )
    assert bogus.status_code == 404


def test_replay_unknown_ids_return_404(client, tmp_path):
    workspace_id = _seed(tmp_path)
    base = f"/api/workspaces/{workspace_id}/quality"
    assert client.get(f"{base}/sample-items/missing/replays").status_code == 404
    assert client.get(f"{base}/replays/missing").status_code == 404
    response = client.post(f"{base}/sample-items/missing/replays", json={})
    assert response.status_code == 404


def test_replay_agent_version_validation(client, tmp_path):
    workspace_id = _seed(tmp_path)
    base = f"/api/workspaces/{workspace_id}/quality"
    # Executor-routed node: an Agent version pin makes no sense.
    response = client.post(f"{base}/sample-items/item-1/replays", json={"agent_version": 1})
    assert response.status_code == 400
    # Contract validation: version numbers are positive.
    assert (
        client.post(f"{base}/sample-items/item-1/replays", json={"agent_version": 0}).status_code
        == 422
    )


def test_replay_anonymous_access_rejected(anon_client):
    base = "/api/workspaces/ws-quality/quality"
    assert anon_client.get(f"{base}/sample-items/item-1/replays").status_code == 401
    assert anon_client.get(f"{base}/replays/r-1").status_code == 401
    assert anon_client.post(f"{base}/sample-items/item-1/replays", json={}).status_code == 401
