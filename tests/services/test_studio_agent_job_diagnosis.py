"""Service-level tests for the studio-agent job observation tools (#329).

Covers the parts the route tests cannot see: run resolution order, log tail /
artifact head truncation, compare semantics, and the session-bound context
authorization matrix (bound token → workspace match, admin, non-member).
"""

from __future__ import annotations

import pytest

from server.app.auth import scoped_tokens
from server.app.services.job_errors import NotFoundError
from server.app.services.studio_agent_job_tools import (
    _ARTIFACT_MAX_CHARS,
    _LOG_TAIL_CHARS,
    StudioAgentJobToolsService,
)
from tests.helpers import publish_builtin_revision
from tests.helpers.seed import insert_job, insert_workspace

_WORKFLOW_KEY = "education_video_problems_generation"


@pytest.fixture
def service(job_db, settings) -> StudioAgentJobToolsService:
    return StudioAgentJobToolsService(job_db, settings)


def _seed_workspace(job_db, workspace_id: str) -> None:
    with job_db.connect() as conn:
        insert_workspace(conn, workspace_id=workspace_id, default_workflow_key=_WORKFLOW_KEY)
    publish_builtin_revision(job_db, workspace_id)


def _seed_job(
    job_db,
    *,
    workspace_id: str,
    job_id: str,
    job_status: str = "failed",
    nodes: list[tuple[str, str, str]],
) -> None:
    with job_db.connect() as conn:
        insert_job(conn, job_id=job_id, workspace_id=workspace_id, workflow_key=_WORKFLOW_KEY)
        conn.execute("update jobs set status=%s where id=%s", (job_status, job_id))
        for node_key, status, error in nodes:
            conn.execute(
                "insert into job_nodes(job_id, node_key, status, error_message)"
                " values (%s, %s, %s, %s)",
                (job_id, node_key, status, error),
            )


def _seed_run(
    job_db,
    *,
    job_id: str,
    node_key: str,
    status: str = "failed",
    error: str = "",
    log_path: str = "",
) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "insert into node_runs(job_id, node_key, status, error_message, log_path)"
            " values (%s, %s, %s, %s, %s) returning id",
            (job_id, node_key, status, error, log_path),
        ).fetchone()
    return int(row["id"])


def _write_log(settings, job_id: str, name: str, text: str) -> str:
    path = settings.jobs_dir / job_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"jobs/{job_id}/{name}"


def test_detail_404_for_job_in_other_workspace(service, job_db) -> None:
    _seed_workspace(job_db, "ws-a")
    _seed_workspace(job_db, "ws-b")
    _seed_job(job_db, workspace_id="ws-b", job_id="job-b", job_status="completed", nodes=[])

    with pytest.raises(NotFoundError):
        service.get_job_detail("ws-a", "job-b")


def test_detail_404_for_unknown_job(service, job_db) -> None:
    _seed_workspace(job_db, "ws-a")
    with pytest.raises(NotFoundError):
        service.get_job_detail("ws-a", "job-missing")


def test_node_logs_run_resolution_order(service, job_db, settings) -> None:
    _seed_workspace(job_db, "ws-runs")
    _seed_job(job_db, workspace_id="ws-runs", job_id="job-1", nodes=[])
    older = _seed_run(
        job_db,
        job_id="job-1",
        node_key="write_script",
        error="first",
        log_path=_write_log(settings, "job-1", "older.log", "older failure"),
    )
    newer = _seed_run(
        job_db,
        job_id="job-1",
        node_key="write_script",
        error="second",
        log_path=_write_log(settings, "job-1", "newer.log", "newer failure"),
    )
    ok = _seed_run(
        job_db,
        job_id="job-1",
        node_key="publish_content",
        status="completed",
        log_path=_write_log(settings, "job-1", "ok.log", "all good"),
    )

    # node_key picks that node's LATEST run.
    by_node = service.get_node_logs("ws-runs", "job-1", node_key="write_script")
    assert by_node["run_id"] == newer
    assert "newer failure" in by_node["log"]
    # Explicit run_id wins over everything.
    explicit = service.get_node_logs("ws-runs", "job-1", node_key="write_script", run_id=older)
    assert explicit["run_id"] == older
    # No selector: the latest FAILED run (not the latest run overall).
    defaulted = service.get_node_logs("ws-runs", "job-1")
    assert defaulted["run_id"] == newer
    assert defaulted["run_id"] != ok


def test_node_logs_tail_truncation(service, job_db, settings) -> None:
    _seed_workspace(job_db, "ws-tail")
    _seed_job(job_db, workspace_id="ws-tail", job_id="job-1", nodes=[])
    text = "head-marker\n" + ("x" * (_LOG_TAIL_CHARS + 500)) + "\ntail-marker\n"
    _seed_run(
        job_db,
        job_id="job-1",
        node_key="write_script",
        log_path=_write_log(settings, "job-1", "big.log", text),
    )

    payload = service.get_node_logs("ws-tail", "job-1")

    assert payload["truncated"] is True
    assert len(payload["log"]) == _LOG_TAIL_CHARS
    assert "tail-marker" in payload["log"]
    assert "head-marker" not in payload["log"]


def test_node_logs_404_when_nothing_ran(service, job_db) -> None:
    _seed_workspace(job_db, "ws-norun")
    _seed_job(job_db, workspace_id="ws-norun", job_id="job-1", nodes=[])

    with pytest.raises(NotFoundError):
        service.get_node_logs("ws-norun", "job-1")
    with pytest.raises(NotFoundError):
        service.get_node_logs("ws-norun", "job-1", run_id=999)


def test_read_artifact_head_truncation(service, job_db, settings) -> None:
    _seed_workspace(job_db, "ws-art")
    _seed_job(job_db, workspace_id="ws-art", job_id="job-1", job_status="completed", nodes=[])
    text = "head-marker\n" + ("y" * (_ARTIFACT_MAX_CHARS + 100)) + "\ntail-marker\n"
    path = settings.jobs_dir / "job-1" / "big.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    payload = service.read_artifact("ws-art", "job-1", "big.json")

    assert payload["truncated"] is True
    assert len(payload["content"]) == _ARTIFACT_MAX_CHARS
    assert payload["content"].startswith("head-marker")
    assert "tail-marker" not in payload["content"]


def test_read_artifact_rejects_path_escape(service, job_db) -> None:
    _seed_workspace(job_db, "ws-esc")
    _seed_job(job_db, workspace_id="ws-esc", job_id="job-1", job_status="completed", nodes=[])

    from server.app.services.job_errors import InvalidOperationError

    with pytest.raises(InvalidOperationError):
        service.read_artifact("ws-esc", "job-1", "../secret")


def test_list_jobs_clamps_limit(service, job_db) -> None:
    _seed_workspace(job_db, "ws-cap")
    for index in range(3):
        _seed_job(
            job_db, workspace_id="ws-cap", job_id=f"job-{index}", job_status="completed", nodes=[]
        )

    payload = service.list_jobs("ws-cap", limit=1)
    assert payload["returned"] == 1
    assert len(payload["jobs"]) == 1
    assert payload["limit"] == 1
    # Absurd limits clamp instead of erroring.
    assert service.list_jobs("ws-cap", limit=10_000)["limit"] == 100


def test_compare_jobs_marks_absent_nodes(service, job_db) -> None:
    _seed_workspace(job_db, "ws-absent")
    _seed_job(
        job_db,
        workspace_id="ws-absent",
        job_id="job-a",
        nodes=[("write_script", "failed", "boom")],
    )
    _seed_job(
        job_db,
        workspace_id="ws-absent",
        job_id="job-b",
        job_status="completed",
        nodes=[("write_script", "completed", ""), ("publish_content", "completed", "")],
    )

    payload = service.compare_jobs("ws-absent", "job-a", "job-b")

    publish = next(n for n in payload["nodes"] if n["node_key"] == "publish_content")
    assert publish["status_a"] == "absent"
    assert publish["status_b"] == "completed"
    assert publish["changed"] is True
    assert payload["summary"]["nodes_changed"] == 2


def _session_user(
    job_db, *, workspace_id: str | None, username: str = "diag-user"
) -> tuple[str, dict]:
    """Create a user and mint/authenticate a scoped token for it; returns
    (user_id, user_dict) so tests can also seed sessions owned by the user."""
    user_id = str(job_db.create_user(username, password_hash=None, role="admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, user_id, workspace_id=workspace_id)
    user = scoped_tokens.authenticate_scoped_token(job_db, token)
    assert user is not None
    return user_id, user


def test_job_context_requires_known_session(service, job_db) -> None:
    _, user = _session_user(job_db, workspace_id=None)
    with pytest.raises(NotFoundError):
        service.get_job_context("sess-missing", user, "job-1")


def test_job_context_bound_token_workspace_mismatch_404(service, job_db) -> None:
    _seed_workspace(job_db, "ws-home")
    _seed_workspace(job_db, "ws-other")
    user_id, bound = _session_user(job_db, workspace_id="ws-home")
    other_session = job_db.create_studio_chat_session("ws-other", user_id, "agent-x")

    with pytest.raises(NotFoundError):
        service.get_job_context(other_session, bound, "job-1")


def test_job_context_rejects_job_outside_session_workspace(service, job_db) -> None:
    _seed_workspace(job_db, "ws-home")
    _seed_workspace(job_db, "ws-other")
    _seed_job(job_db, workspace_id="ws-other", job_id="job-b", nodes=[])
    user_id, bound = _session_user(job_db, workspace_id="ws-home")
    session_id = job_db.create_studio_chat_session("ws-home", user_id, "agent-x")

    with pytest.raises(NotFoundError):
        service.get_job_context(session_id, bound, "job-b")


def test_job_context_focus_falls_back_to_failed_node(service, job_db) -> None:
    _seed_workspace(job_db, "ws-focus")
    _seed_job(
        job_db,
        workspace_id="ws-focus",
        job_id="job-1",
        nodes=[("intake_knowledge_points", "completed", ""), ("write_script", "failed", "boom")],
    )
    user_id, bound = _session_user(job_db, workspace_id="ws-focus")
    session_id = job_db.create_studio_chat_session("ws-focus", user_id, "agent-x")

    payload = service.get_job_context(session_id, bound, "job-1")

    assert payload["focus_node_key"] == "write_script"
    assert payload["workspace_id"] == "ws-focus"
    # Explicit node_key wins over the fallback.
    explicit = service.get_job_context(
        session_id, bound, "job-1", node_key="intake_knowledge_points"
    )
    assert explicit["focus_node_key"] == "intake_knowledge_points"
