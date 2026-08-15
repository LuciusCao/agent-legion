"""Quality loop routes: auth, batch creation, labeling, stats (schema v28)."""

from __future__ import annotations

import pytest

from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema

WORKSPACE = "ws-quality"
BASE = f"/api/workspaces/{WORKSPACE}/quality"


def _seed_runs() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow') on conflict do nothing",
            (WORKSPACE, WORKSPACE),
        )
        for index, (status, node_key) in enumerate(
            [("completed", "node-a"), ("completed", "node-a"), ("failed", "node-b")]
        ):
            job_id = f"job-{index}"
            conn.execute(
                "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
                " values (%s, %s, 'wf-a', 'test', %s)",
                (job_id, WORKSPACE, job_id),
            )
            conn.execute(
                "insert into node_runs(id, job_id, node_key, status) values (%s, %s, %s, %s)",
                (index + 1, job_id, node_key, status),
            )


def _create_batch(client) -> dict:
    response = client.post(
        f"{BASE}/sample-batches",
        json={"name": "batch-1", "sample_size": 10, "seed": "seed-1"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_and_detail_flow(client):
    _seed_runs()
    batch = _create_batch(client)
    assert batch["sampled_count"] == 3
    assert batch["seed"] == "seed-1"
    assert batch["filters"] == {}

    listing = client.get(f"{BASE}/sample-batches")
    assert listing.status_code == 200
    assert [b["id"] for b in listing.json()["batches"]] == [batch["id"]]

    detail = client.get(f"{BASE}/sample-batches/{batch['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all(item["current_label"] is None for item in body["items"])
    assert body["batch"]["name"] == "batch-1"


def test_label_latest_wins_and_stats(client):
    _seed_runs()
    batch = _create_batch(client)
    detail = client.get(f"{BASE}/sample-batches/{batch['id']}").json()
    item = next(i for i in detail["items"] if i["node_key"] == "node-a")

    response = client.post(
        f"{BASE}/sample-items/{item['id']}/labels",
        json={"verdict": "bad", "reason_codes": ["fact_error"], "note": "wrong"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["label"]["verdict"] == "bad"

    response = client.post(
        f"{BASE}/sample-items/{item['id']}/labels",
        json={"verdict": "good"},
    )
    assert response.status_code == 200

    detail = client.get(f"{BASE}/sample-batches/{batch['id']}").json()
    labeled = next(i for i in detail["items"] if i["id"] == item["id"])
    assert labeled["current_label"]["verdict"] == "good"

    item_detail = client.get(f"{BASE}/sample-items/{item['id']}")
    assert item_detail.status_code == 200
    assert len(item_detail.json()["labels"]) == 2
    assert item_detail.json()["artifacts"] == []

    stats = client.get(f"{BASE}/sample-batches/{batch['id']}/stats")
    assert stats.status_code == 200
    groups = {g["node_key"]: g for g in stats.json()["groups"]}
    assert groups["node-a"]["runs"] == 2
    assert groups["node-a"]["succeeded"] == 2
    assert groups["node-a"]["success_rate"] == 1.0
    assert groups["node-a"]["labeled"] == 1
    assert groups["node-a"]["good"] == 1
    assert groups["node-a"]["good_rate"] == 1.0
    assert groups["node-b"]["runs"] == 1
    assert groups["node-b"]["succeeded"] == 0
    assert groups["node-b"]["labeled"] == 0
    assert groups["node-b"]["good_rate"] is None
    assert groups["node-b"]["confusion_matrix"] is None


def test_stats_confusion_matrix(client):
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'demo_workflow') on conflict do nothing",
            (WORKSPACE, WORKSPACE),
        )
        # (node_key, status, failure_detail): review pass ×2, review reject ×2,
        # plus one non-review node whose items stay unlabeled.
        runs = [
            ("review_key_info", "completed", ""),
            ("review_key_info", "completed", ""),
            ("review_key_info", "failed", "review_rejected"),
            ("review_key_info", "failed", "review_rejected"),
            ("generate_key_info", "completed", ""),
        ]
        for index, (node_key, status, failure_detail) in enumerate(runs):
            job_id = f"job-cm-{index}"
            conn.execute(
                "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
                " values (%s, %s, 'wf-a', 'test', %s)",
                (job_id, WORKSPACE, job_id),
            )
            conn.execute(
                "insert into node_runs(id, job_id, node_key, status, failure_detail)"
                " values (%s, %s, %s, %s, %s)",
                (index + 1, job_id, node_key, status, failure_detail),
            )
    batch = _create_batch(client)
    assert batch["sampled_count"] == 5
    detail = client.get(f"{BASE}/sample-batches/{batch['id']}").json()
    by_run = {item["node_run_id"]: item["id"] for item in detail["items"]}
    # tn=good+pass, fn=bad+pass, fp=good+reject, tp=bad+reject.
    for run_id, verdict in [(1, "good"), (2, "bad"), (3, "good"), (4, "bad")]:
        response = client.post(
            f"{BASE}/sample-items/{by_run[run_id]}/labels",
            json={"verdict": verdict},
        )
        assert response.status_code == 200, response.text

    stats = client.get(f"{BASE}/sample-batches/{batch['id']}/stats")
    assert stats.status_code == 200
    groups = {g["node_key"]: g for g in stats.json()["groups"]}
    matrix = groups["review_key_info"]["confusion_matrix"]
    assert matrix["tp"] == 1
    assert matrix["fp"] == 1
    assert matrix["fn"] == 1
    assert matrix["tn"] == 1
    assert matrix["precision"] == 0.5
    assert matrix["recall"] == 0.5
    assert matrix["accuracy"] == 0.5
    assert groups["generate_key_info"]["confusion_matrix"] is None


def test_invalid_reason_code_rejected(client):
    _seed_runs()
    batch = _create_batch(client)
    detail = client.get(f"{BASE}/sample-batches/{batch['id']}").json()
    item_id = detail["items"][0]["id"]
    response = client.post(
        f"{BASE}/sample-items/{item_id}/labels",
        json={"verdict": "bad", "reason_codes": ["nonsense"]},
    )
    assert response.status_code == 422


def test_unknown_batch_and_item_return_404(client):
    _seed_runs()
    assert client.get(f"{BASE}/sample-batches/missing").status_code == 404
    assert client.get(f"{BASE}/sample-batches/missing/stats").status_code == 404
    assert client.get(f"{BASE}/sample-items/missing").status_code == 404
    response = client.post(
        f"{BASE}/sample-items/missing/labels",
        json={"verdict": "good"},
    )
    assert response.status_code == 404


def test_cross_workspace_batch_not_visible(client):
    _seed_runs()
    batch = _create_batch(client)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws-other', 'ws-other', 'demo_workflow')"
            " on conflict do nothing"
        )
    other = "/api/workspaces/ws-other/quality"
    assert client.get(f"{other}/sample-batches/{batch['id']}").status_code == 404


def test_anonymous_access_rejected(anon_client):
    assert anon_client.get(f"{BASE}/sample-batches").status_code == 401
    response = anon_client.post(
        f"{BASE}/sample-batches",
        json={"name": "b", "sample_size": 5},
    )
    assert response.status_code == 401
