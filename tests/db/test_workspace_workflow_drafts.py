"""Schema v61: workspace_workflow_drafts roundtrip (Studio YAML draft store)."""

from __future__ import annotations

import time

import pytest

from server.app.jobs.queries.workflow_drafts import DraftConflictError


def test_upsert_then_get_roundtrip(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft")

    stored = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    assert stored["definition_yaml"] == "key: wf\n"
    assert stored["created_at"]
    assert stored["updated_at"]
    loaded = job_db.get_workspace_workflow_draft(workspace["id"])
    assert loaded is not None
    assert loaded["definition_yaml"] == "key: wf\n"


def test_get_returns_none_without_a_draft(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-empty")

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_draft_state_read_returns_content_and_generation_in_one_snapshot(job_db) -> None:
    """#1226 六轮 codex P1：草稿内容 + 代次的原子读（单查询同一快照）——
    空态也返回一行（内容字段全 null、代次为 workspace 当前值），行在时
    与草稿行/代次行同源。"""
    workspace = job_db.create_workspace("ws-draft-state")

    empty = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert empty["definition_yaml"] is None
    assert empty["updated_at"] is None
    assert empty["generation"] == 0

    stored = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    state = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert state["definition_yaml"] == "key: wf\n"
    assert state["updated_at"] == stored["updated_at"]
    assert state["generation"] == stored["generation"] == 1

    # 发布删行后：内容回 null，代次仍是推进后的当前值（同一快照）。
    with job_db.connect() as conn:
        job_db.delete_workspace_workflow_draft(conn, workspace["id"])
    post_publish = job_db.get_workspace_workflow_draft_state(workspace["id"])
    assert post_publish["definition_yaml"] is None
    assert post_publish["generation"] == 2


def test_rejected_conditional_bump_leaves_no_generation_row(job_db) -> None:
    """#1226 对抗复验 P3：base>0 而代次行本就不存在的必败 CAS 写入不再兜底
    插 generation=0 行——被拒写入不留残余（base=0 的首存仍需兜底行做并发
    首存的锁目标，不受此限）。"""
    workspace = job_db.create_workspace("ws-draft-no-residue")

    with pytest.raises(DraftConflictError):
        job_db.upsert_workspace_workflow_draft_if_unchanged(workspace["id"], "key: wf\n", 5)

    with job_db.connect() as conn:
        row = conn.execute(
            "select 1 from workspace_workflow_draft_generation where workspace_id=%s",
            (workspace["id"],),
        ).fetchone()
    assert row is None


def test_upsert_is_idempotent_and_advances_updated_at(job_db) -> None:
    workspace = job_db.create_workspace("ws-draft-upsert")

    first = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    again = job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")
    assert again["created_at"] == first["created_at"]

    # 保留的墙钟等待：断言 updated_at 严格前移，需要真实时钟推进。
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
    workspace = job_db.create_workspace("ws-draft-cascade")
    job_db.upsert_workspace_workflow_draft(workspace["id"], "key: wf\n")

    job_db.delete_workspace(workspace["id"])

    assert job_db.get_workspace_workflow_draft(workspace["id"]) is None


def test_workspace_delete_cancels_queued_requests(job_db) -> None:
    """#759：workspace 删除后其 queued 请求行随之物理消失（on delete
    cascade）——钉住升级路径修复依赖的 cascade 契约。"""
    workspace = job_db.create_workspace("ws-cancel-cascade")
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
