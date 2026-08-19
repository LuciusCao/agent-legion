"""JobPauseService：execution_paused 批量暂停/恢复的语义与选择集解析。"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_pause import JobPauseService

_NODE_KEYS = ["intake_knowledge_points", "write_script", "review_script"]


@pytest.fixture
def pause_service(job_db):
    return JobPauseService(job_db)


def _create_job(job_db, workspace_id: str, source_id: str) -> dict[str, Any]:
    batch = job_db.create_batch(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace_id,
    )
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        batch_id=batch["id"],
        title=source_id,
        node_keys=_NODE_KEYS,
        workspace_id=workspace_id,
    )


@pytest.fixture
def seeded(job_db):
    ws = job_db.create_workspace(
        "pause-batch", default_workflow_key="education_video_problems_generation"
    )
    ws_id = str(ws["id"])
    jobs = {
        key: _create_job(job_db, ws_id, f"pause-{key}") for key in ("a", "b", "terminal", "target")
    }
    job_db.update_job_status(jobs["terminal"]["id"], "failed", "boom")
    # target mimics a run-to pause owned by the continue flow.
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set status='paused', execution_paused=1,"
            " pause_reason='target_reached' where id=%s",
            (jobs["target"]["id"],),
        )
    other = job_db.create_workspace(
        "pause-other", default_workflow_key="education_video_problems_generation"
    )
    foreign = _create_job(job_db, str(other["id"]), "pause-foreign")
    return {"ws_id": ws_id, "jobs": jobs, "foreign": foreign}


def _flag(job_db, job_id: str) -> dict[str, Any]:
    row = job_db.get_job(job_id)
    return {
        "execution_paused": row["execution_paused"],
        "pause_reason": row["pause_reason"],
        "status": row["status"],
    }


def test_batch_pause_marks_selection_with_reason_and_operator(pause_service, job_db, seeded):
    ids = [str(seeded["jobs"]["a"]["id"]), str(seeded["jobs"]["b"]["id"])]
    results = pause_service.batch_pause(seeded["ws_id"], ids, "smoke hold", operator="user:1")

    assert [r["status"] for r in results] == ["succeeded", "succeeded"]
    assert {r["operation"] for r in results} == {"pause"}
    for job_id in ids:
        flag = _flag(job_db, job_id)
        assert flag["execution_paused"] == 1
        assert flag["pause_reason"] == "smoke hold (user:1)"
        assert flag["status"] == "queued"


def test_batch_pause_skips_terminal_and_already_paused(pause_service, job_db, seeded):
    terminal_id = str(seeded["jobs"]["terminal"]["id"])
    target_id = str(seeded["jobs"]["target"]["id"])
    results = pause_service.batch_pause(
        seeded["ws_id"], [terminal_id, target_id], "hold", operator="user:1"
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id[terminal_id]["status"] == "skipped"
    assert by_id[terminal_id]["reason_code"] == "terminal"
    assert by_id[target_id]["status"] == "skipped"
    assert by_id[target_id]["reason_code"] == "already_paused"
    # Terminal and run-to-paused jobs stay untouched.
    assert _flag(job_db, terminal_id)["execution_paused"] == 0
    assert _flag(job_db, target_id)["pause_reason"] == "target_reached"


def test_pause_unknown_and_foreign_results(pause_service, seeded):
    with pytest.raises(JobOperationError) as missing_exc:
        pause_service.pause(seeded["ws_id"], "missing-job")
    assert missing_exc.value.reason_code == "not_found"
    with pytest.raises(JobOperationError) as foreign_exc:
        pause_service.pause(seeded["ws_id"], str(seeded["foreign"]["id"]))
    assert foreign_exc.value.reason_code == "wrong_workspace"

    results = pause_service.batch_pause(
        seeded["ws_id"], ["missing-job", str(seeded["foreign"]["id"])], None
    )
    assert [r["status"] for r in results] == ["failed", "failed"]
    assert [r["reason_code"] for r in results] == ["not_found", "wrong_workspace"]


def test_batch_pause_resolves_filter_selection(pause_service, job_db, seeded):
    results = pause_service.batch_pause(
        seeded["ws_id"],
        job_filter=JobListFilter(search="pause-"),
        exclude_ids=[str(seeded["jobs"]["b"]["id"])],
    )

    paused_ids = {r["job_id"] for r in results if r["status"] == "succeeded"}
    assert paused_ids == {str(seeded["jobs"]["a"]["id"])}
    assert _flag(job_db, str(seeded["jobs"]["b"]["id"]))["execution_paused"] == 0


def test_batch_resume_restores_flag_and_paused_status(pause_service, job_db, seeded):
    ws_id = seeded["ws_id"]
    job_a = str(seeded["jobs"]["a"]["id"])
    job_b = str(seeded["jobs"]["b"]["id"])
    pause_service.batch_pause(ws_id, [job_a, job_b], "hold", operator="user:1")
    # A job whose nodes all settle while paused is projected to status='paused'.
    with job_db.connect() as conn:
        conn.execute("update jobs set status='paused' where id=%s", (job_b,))

    results = pause_service.batch_resume(ws_id, [job_a, job_b])

    assert [r["status"] for r in results] == ["succeeded", "succeeded"]
    assert {r["operation"] for r in results} == {"resume"}
    assert _flag(job_db, job_a) == {
        "execution_paused": 0,
        "pause_reason": "",
        "status": "queued",
    }
    assert _flag(job_db, job_b) == {
        "execution_paused": 0,
        "pause_reason": "",
        "status": "queued",
    }


def test_batch_resume_skips_unpaused_and_target_reached(pause_service, job_db, seeded):
    ws_id = seeded["ws_id"]
    unpaused_id = str(seeded["jobs"]["a"]["id"])
    target_id = str(seeded["jobs"]["target"]["id"])

    results = pause_service.batch_resume(ws_id, [unpaused_id, target_id])

    by_id = {r["job_id"]: r for r in results}
    assert by_id[unpaused_id]["status"] == "skipped"
    assert by_id[unpaused_id]["reason_code"] == "not_paused"
    assert by_id[target_id]["status"] == "skipped"
    assert by_id[target_id]["reason_code"] == "target_reached"
    assert _flag(job_db, target_id)["execution_paused"] == 1


def test_resume_filter_selection_by_paused_flag(pause_service, job_db, seeded):
    ws_id = seeded["ws_id"]
    pause_service.batch_pause(ws_id, [str(seeded["jobs"]["a"]["id"])], None, operator="user:1")

    results = pause_service.batch_resume(ws_id, job_filter=JobListFilter(paused=True))

    resumed = {r["job_id"] for r in results if r["status"] == "succeeded"}
    assert resumed == {str(seeded["jobs"]["a"]["id"])}
    assert _flag(job_db, str(seeded["jobs"]["a"]["id"]))["execution_paused"] == 0
