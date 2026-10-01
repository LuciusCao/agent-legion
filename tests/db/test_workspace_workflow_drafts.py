"""Schema v61: workspace_workflow_drafts roundtrip (Studio YAML draft store)."""

from __future__ import annotations

import time


def test_upsert_then_get_roundtrip(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft", default_workflow_key="wf")

    stored = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    assert stored["definition_yaml"] == "key: wf\n"
    assert stored["created_at"]
    assert stored["updated_at"]
    loaded = job_db.get_workspace_workflow_draft(workspace["id"])
    assert loaded is not None
    assert loaded["definition_yaml"] == "key: wf\n"


def test_get_returns_none_without_a_draft(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-empty", default_workflow_key="wf")

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_upsert_is_idempotent_and_advances_updated_at(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-upsert", default_workflow_key="wf")

    first = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    again = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    assert again["created_at"] == first["created_at"]

    time.sleep(0.02)
    updated = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf2\n")
    assert updated["definition_yaml"] == "key: wf2\n"
    # created_at survives the update; updated_at moves forward.
    assert updated["created_at"] == first["created_at"]
    assert updated["updated_at"] > first["updated_at"]

    loaded = job_db.get_workspace_workflow_draft(workspace["id"])
    assert loaded is not None
    assert loaded["definition_yaml"] == "key: wf2\n"


def test_workspace_delete_cascades_to_the_draft(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-cascade", default_workflow_key="wf")
    job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    job_db.delete_workspace(workspace["id"])

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_workspace_delete_cancels_queued_requests(job_db) -> None:
    """#759：workspace 删除后其 queued 请求行随之物理消失（on delete
    cascade）——钉住升级路径修复依赖的 cascade 契约。"""
    workspace = job_db.create_workspace("ws-cancel-cascade", default_workflow_key="wf")
    batch = job_db.create_run("wf", "batch_by_ids", {"ids": ["1"]}, workspace["id"])
    job = job_db.create_job(
        workflow_key="wf",
        source_type="question",
        source_id="1",
        run_id=batch["id"],
        title="j1",
        node_keys=["n1"],
        workspace_id=workspace["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-ws-queued', %s, %s, 'n1',"
            " 'generator-v1', 'sha256:whatever', 1, 'queued', current_timestamp, '{}')",
            (workspace["id"], job["id"]),
        )

    job_db.delete_workspace(workspace["id"])

    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id='exec-ws-queued'"
        ).fetchone()
    assert row is None
