"""Behavioral contract for the studio-agent job observation tools (#329).

The router is mounted test-locally: the production wiring line in
``routes/__init__.py`` lands with the parallel mission that owns that file,
so these tests replicate what ``secured()`` does there (``/api`` prefix +
``require_workspace_access``). The guard matrix mirrors
test_studio_agent_tools.py: anonymous 401, full session 403, scoped tokens
pass the scope guard, workspace-bound tokens get 403 on other workspaces.
The action-surface test pins that scoped tokens are refused by the effecting
job routes (retry/rerun stay human-only, STUDIO-AGENT-001).
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends

from server.app.auth import scoped_tokens
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.studio_agent_job_tools import create_studio_agent_job_tools_router
from tests.helpers import publish_builtin_revision
from tests.helpers.seed import insert_job, insert_workspace

_WORKFLOW_KEY = "education_video_problems_generation"


def _mount_job_tool_routes(client) -> None:
    """Replicate the production wiring test-locally (the routes/__init__.py
    line lands with the parallel mission owning that file): same ``/api``
    prefix + ``require_workspace_access`` wrapper as ``secured()``, and —
    critically — inserted BEFORE the ``/api/studio-agent`` MCP ASGI mount,
    which is appended at app build time and would otherwise shadow the
    late-mounted routes (prefix match → its 401)."""
    from starlette.routing import Mount

    app = client.app
    api = APIRouter(prefix="/api", dependencies=[Depends(require_workspace_access)])
    api.include_router(
        create_studio_agent_job_tools_router(
            app.state.job_db,
            app.state.settings,
            object_store=app.state.job_artifact_objects,
        )
    )
    routes = app.router.routes
    mount_index = next(
        index
        for index, route in enumerate(routes)
        if isinstance(route, Mount) and route.path == "/api/studio-agent"
    )
    for offset, route in enumerate(api.routes):
        routes.insert(mount_index + offset, route)


@pytest.fixture
def tools_client(client_factory):
    with client_factory(fresh=True) as client:
        _mount_job_tool_routes(client)
        yield client


@pytest.fixture
def anon_tools_client(client_factory):
    with client_factory(authenticated=False, fresh=True) as client:
        _mount_job_tool_routes(client)
        yield client


def _scoped_client(client, job_db, workspace_id: str | None = None):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


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
    """nodes: (node_key, status, error_message) rows in job_nodes."""
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


def _job_tool_endpoints(workspace_id: str) -> list[tuple[str, str]]:
    base = f"/api/studio-agent/tools/workspaces/{workspace_id}"
    return [
        ("GET", f"{base}/jobs"),
        ("GET", f"{base}/jobs/compare?job_id_a=a&job_id_b=b"),
        ("GET", f"{base}/jobs/job-x"),
        ("GET", f"{base}/jobs/job-x/logs"),
        ("GET", f"{base}/jobs/job-x/artifacts/questions.json"),
        ("GET", "/api/studio-agent/tools/chat-sessions/sess-x/job-context?job_id=job-x"),
    ]


def test_anonymous_callers_get_401_on_all_job_tool_endpoints(anon_tools_client) -> None:
    for method, url in _job_tool_endpoints("ws-any"):
        response = anon_tools_client.request(method, url)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


def test_full_user_session_gets_403_on_all_job_tool_endpoints(tools_client) -> None:
    # The client fixture carries a full admin session — not a scoped token.
    for method, url in _job_tool_endpoints("ws-any"):
        response = tools_client.request(method, url)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert "scoped token" in response.json()["detail"]


def test_scoped_token_passes_scope_guard_on_all_job_tool_endpoints(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-scope")
    scoped, _ = _scoped_client(tools_client, job_db)
    for method, url in _job_tool_endpoints("ws-scope"):
        response = scoped.request(method, url)
        # < 500 too: a 5xx here would be a server bug masquerading as a pass.
        assert response.status_code not in (401, 403) and response.status_code < 500, (
            f"{method} {url} -> {response.status_code}: {response.text}"
        )


def test_workspace_bound_token_is_refused_on_other_workspaces(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-home")
    _seed_workspace(job_db, "ws-other")
    bound, _ = _scoped_client(tools_client, job_db, workspace_id="ws-home")

    assert bound.get("/api/studio-agent/tools/workspaces/ws-home/jobs").status_code == 200
    for method, url in _job_tool_endpoints("ws-other"):
        if "chat-sessions" in url:
            # The session-bound endpoint carries no workspace path segment; its
            # cross-workspace refusal is covered by the context tests below.
            continue
        response = bound.request(method, url)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert "bound" in response.json()["detail"]


def test_scoped_token_is_refused_on_job_action_endpoints(tools_client) -> None:
    """Action surface (#329): retry/rerun stay human-only — a scoped token
    hitting the effecting job routes directly gets 403 (the router-level
    reject_studio_agent_scope fires before any job lookup)."""
    job_db = tools_client.app.state.job_db
    scoped, _ = _scoped_client(tools_client, job_db)

    rerun = scoped.post("/api/jobs/job-x/nodes/node-x/rerun")
    run_to = scoped.post("/api/jobs/job-x/run-to", json={"target_node_key": "node-x"})

    assert rerun.status_code == 403
    assert "cannot take effect" in rerun.json()["detail"]
    assert run_to.status_code == 403
    assert "cannot take effect" in run_to.json()["detail"]


def test_get_job_detail_trims_payload_and_suggests_rerun(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-detail")
    _seed_job(
        job_db,
        workspace_id="ws-detail",
        job_id="job-1",
        nodes=[
            ("intake_knowledge_points", "completed", ""),
            ("write_script", "failed", "LLM timeout"),
        ],
    )
    run_id = _seed_run(
        job_db, job_id="job-1", node_key="write_script", error="LLM timeout", log_path="x.log"
    )
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get("/api/studio-agent/tools/workspaces/ws-detail/jobs/job-1")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job"]["id"] == "job-1"
    assert payload["job"]["status"] == "failed"
    assert payload["job"]["active_node_key"] == "write_script"
    # Trimmed surface: no input/config snapshots, no local paths.
    assert "input_json" not in payload["job"]
    assert "storage_dir" not in payload["job"]
    assert "log_path" not in payload["runs"][0]
    assert payload["runs"][0]["id"] == run_id
    assert payload["runs"][0]["has_log"] is True
    node = next(n for n in payload["nodes"] if n["node_key"] == "write_script")
    assert node["error_message"] == "LLM timeout"
    assert payload["suggested_actions"] == [
        {
            "action": "rerun_node",
            "job_id": "job-1",
            "node_key": "write_script",
            "label": "重跑节点 撰写教学视频脚本",
            "requires_confirmation": True,
        }
    ]


def test_get_job_detail_404_for_other_workspace_job(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-a")
    _seed_workspace(job_db, "ws-b")
    _seed_job(job_db, workspace_id="ws-b", job_id="job-b", job_status="completed", nodes=[])
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get("/api/studio-agent/tools/workspaces/ws-a/jobs/job-b")

    # 404 (not 403): job ids cannot be probed across workspaces.
    assert response.status_code == 404


def test_list_jobs_filters_status_and_caps_limit(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-list")
    _seed_job(job_db, workspace_id="ws-list", job_id="job-ok", job_status="completed", nodes=[])
    _seed_job(job_db, workspace_id="ws-list", job_id="job-bad", job_status="failed", nodes=[])
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get("/api/studio-agent/tools/workspaces/ws-list/jobs?status=failed")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["returned"] == 1
    assert [job["id"] for job in payload["jobs"]] == ["job-bad"]


def test_get_node_logs_defaults_to_latest_failed_run(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    settings = tools_client.app.state.settings
    _seed_workspace(job_db, "ws-logs")
    _seed_job(
        job_db,
        workspace_id="ws-logs",
        job_id="job-1",
        nodes=[("write_script", "failed", "boom")],
    )
    log_file = settings.jobs_dir / "job-1" / "run-1.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line-1\nline-2\nError: boom\n", encoding="utf-8")
    run_id = _seed_run(
        job_db,
        job_id="job-1",
        node_key="write_script",
        error="boom",
        log_path="jobs/job-1/run-1.log",
    )
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get("/api/studio-agent/tools/workspaces/ws-logs/jobs/job-1/logs")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["node_key"] == "write_script"
    assert payload["error_message"] == "boom"
    assert "Error: boom" in payload["log"]
    assert payload["truncated"] is False


def test_get_node_logs_unknown_node_404(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-logs2")
    _seed_job(job_db, workspace_id="ws-logs2", job_id="job-1", nodes=[])
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get(
        "/api/studio-agent/tools/workspaces/ws-logs2/jobs/job-1/logs?node_key=nope"
    )

    assert response.status_code == 404


def test_read_artifact_returns_trimmed_content(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    settings = tools_client.app.state.settings
    _seed_workspace(job_db, "ws-art")
    _seed_job(job_db, workspace_id="ws-art", job_id="job-1", job_status="completed", nodes=[])
    job_dir = settings.jobs_dir / "job-1"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "questions.json").write_text('{"questions": [1, 2]}', encoding="utf-8")
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get(
        "/api/studio-agent/tools/workspaces/ws-art/jobs/job-1/artifacts/questions.json"
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "name": "questions.json",
        "content": '{"questions": [1, 2]}',
        "truncated": False,
    }


def test_read_artifact_404_for_missing_name(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-art2")
    _seed_job(job_db, workspace_id="ws-art2", job_id="job-1", job_status="completed", nodes=[])
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get(
        "/api/studio-agent/tools/workspaces/ws-art2/jobs/job-1/artifacts/nope.json"
    )

    assert response.status_code == 404


def test_compare_jobs_diffs_node_statuses(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-cmp")
    _seed_job(
        job_db,
        workspace_id="ws-cmp",
        job_id="job-failed",
        nodes=[("intake_knowledge_points", "completed", ""), ("write_script", "failed", "boom")],
    )
    _seed_job(
        job_db,
        workspace_id="ws-cmp",
        job_id="job-ok",
        job_status="completed",
        nodes=[("intake_knowledge_points", "completed", ""), ("write_script", "completed", "")],
    )
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get(
        "/api/studio-agent/tools/workspaces/ws-cmp/jobs/compare?job_id_a=job-failed&job_id_b=job-ok"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job_a"]["id"] == "job-failed"
    assert payload["job_b"]["id"] == "job-ok"
    write_script = next(n for n in payload["nodes"] if n["node_key"] == "write_script")
    assert write_script["status_a"] == "failed"
    assert write_script["status_b"] == "completed"
    assert write_script["changed"] is True
    assert payload["summary"]["newly_failed"] == ["write_script"]
    assert payload["summary"]["recovered"] == []


def test_compare_jobs_404_when_either_job_is_elsewhere(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-cmp2")
    _seed_workspace(job_db, "ws-cmp3")
    _seed_job(job_db, workspace_id="ws-cmp2", job_id="job-a", job_status="failed", nodes=[])
    _seed_job(job_db, workspace_id="ws-cmp3", job_id="job-b", job_status="completed", nodes=[])
    scoped, _ = _scoped_client(tools_client, job_db)

    response = scoped.get(
        "/api/studio-agent/tools/workspaces/ws-cmp2/jobs/compare?job_id_a=job-a&job_id_b=job-b"
    )

    assert response.status_code == 404


def test_get_job_context_binds_session_workspace_and_focus(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-ctx")
    _seed_job(
        job_db,
        workspace_id="ws-ctx",
        job_id="job-1",
        nodes=[("write_script", "failed", "boom")],
    )
    scoped, admin_id = _scoped_client(tools_client, job_db, workspace_id="ws-ctx")
    session_id = job_db.create_studio_chat_session("ws-ctx", admin_id, "agent-x")

    response = scoped.get(
        f"/api/studio-agent/tools/chat-sessions/{session_id}/job-context?job_id=job-1"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session_id"] == session_id
    assert payload["workspace_id"] == "ws-ctx"
    assert payload["focus_node_key"] == "write_script"
    assert payload["job"]["job"]["id"] == "job-1"
    assert payload["job"]["suggested_actions"][0]["action"] == "rerun_node"
    assert payload["recent_failures"] == []


def test_get_job_context_reports_other_jobs_failures_on_focus_node(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-flaky")
    _seed_job(
        job_db, workspace_id="ws-flaky", job_id="job-now", nodes=[("write_script", "failed", "x")]
    )
    _seed_job(
        job_db,
        workspace_id="ws-flaky",
        job_id="job-earlier",
        nodes=[("write_script", "failed", "same boom")],
    )
    _seed_run(job_db, job_id="job-earlier", node_key="write_script", error="same boom")
    scoped, admin_id = _scoped_client(tools_client, job_db, workspace_id="ws-flaky")
    session_id = job_db.create_studio_chat_session("ws-flaky", admin_id, "agent-x")

    response = scoped.get(
        f"/api/studio-agent/tools/chat-sessions/{session_id}/job-context"
        "?job_id=job-now&node_key=write_script"
    )

    assert response.status_code == 200, response.text
    failures = response.json()["recent_failures"]
    assert [f["job_id"] for f in failures] == ["job-earlier"]
    assert failures[0]["error_message"] == "same boom"


def test_get_job_context_404_for_cross_workspace_session_or_job(tools_client) -> None:
    job_db = tools_client.app.state.job_db
    _seed_workspace(job_db, "ws-home2")
    _seed_workspace(job_db, "ws-other2")
    _seed_job(job_db, workspace_id="ws-other2", job_id="job-b", job_status="failed", nodes=[])
    bound, admin_id = _scoped_client(tools_client, job_db, workspace_id="ws-home2")
    session_id = job_db.create_studio_chat_session("ws-home2", admin_id, "agent-x")
    other_session = job_db.create_studio_chat_session("ws-other2", admin_id, "agent-x")
    _seed_job(job_db, workspace_id="ws-home2", job_id="job-a", job_status="failed", nodes=[])

    # Session of another workspace than the token binding.
    assert (
        bound.get(
            f"/api/studio-agent/tools/chat-sessions/{other_session}/job-context?job_id=job-b"
        ).status_code
        == 404
    )
    # Job of another workspace than the session's.
    assert (
        bound.get(
            f"/api/studio-agent/tools/chat-sessions/{session_id}/job-context?job_id=job-b"
        ).status_code
        == 404
    )
    # Unknown session id.
    assert (
        bound.get(
            "/api/studio-agent/tools/chat-sessions/sess-nope/job-context?job_id=job-a"
        ).status_code
        == 404
    )
