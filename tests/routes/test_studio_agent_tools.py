"""Behavioral contract for the studio-agent tool surface (/api/studio-agent/tools/*).

Every tool endpoint requires a studio-agent scoped token (STUDIO-AGENT-001):
anonymous callers get 401 and full user sessions get 403, while scoped
tokens pass the scope guard (business errors may still apply). Draft writes
are attributed with ``created_by=f"studio-agent:{user_id}"``; the workflow
register endpoint triggers the chunk-1 scan hot-reload after the catalog row
commits. The endpoint inventory in ``_tool_endpoints`` is the enumeration
backstop: new tool endpoints must be added here.
"""

from __future__ import annotations

from server.app.auth import scoped_tokens

_NODE_CODE = "def run(job, job_dir, runtime):\n    return {}\n"
_WORKFLOW_KEY = "education_video_problems_generation"
_NODE_KEY = "intake_knowledge_points"


def _scoped_client(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


def _create_workspace(client, name: str = "Studio Tools") -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": name, "default_workflow_key": _WORKFLOW_KEY},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def _active_yaml(scoped, workspace_id: str) -> str:
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active")
    assert response.status_code == 200, response.text
    return str(response.json()["definition_yaml"])


def _tool_endpoints(workspace_id: str) -> list[tuple[str, str, dict | None]]:
    base = f"/api/studio-agent/tools/workspaces/{workspace_id}"
    return [
        ("POST", f"{base}/workflow/validate", {"definition_yaml": ""}),
        ("POST", f"{base}/workflow/compare", {"definition_yaml": ""}),
        (
            "PUT",
            f"{base}/workflows/wf/nodes/node/code/draft",
            {"code": "not python"},
        ),
        ("PUT", "/api/studio-agent/tools/agent-definitions/agent-x/draft", {}),
        ("POST", "/api/studio-agent/tools/workflows/register", {"key": "k", "label": "K"}),
        ("GET", f"{base}/workflow/active", None),
        ("GET", "/api/studio-agent/tools/workflows", None),
        ("GET", f"{base}/workflows/wf/nodes/node/code", None),
        ("GET", "/api/studio-agent/tools/chat-sessions/session-x/context", None),
    ]


def test_anonymous_callers_get_401_on_all_tool_endpoints(client, job_db) -> None:
    del job_db
    workspace_id = _create_workspace(client)
    anon = client.__class__(client.app)
    for method, url, payload in _tool_endpoints(workspace_id):
        response = anon.request(method, url, json=payload)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


def test_full_user_session_gets_403_on_all_tool_endpoints(client, job_db) -> None:
    del job_db
    workspace_id = _create_workspace(client)
    # The client fixture carries a full admin session — not a scoped token.
    for method, url, payload in _tool_endpoints(workspace_id):
        response = client.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert "scoped token" in response.json()["detail"]


def test_scoped_token_passes_scope_guard_on_all_tool_endpoints(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    for method, url, payload in _tool_endpoints(workspace_id):
        response = scoped.request(method, url, json=payload)
        # < 500 too: a 5xx here would be a server bug masquerading as a pass.
        assert response.status_code not in (401, 403) and response.status_code < 500, (
            f"{method} {url} -> {response.status_code}: {response.text}"
        )


def test_workspace_bound_token_is_refused_on_other_workspaces(client, job_db) -> None:
    """Schema v45: a run token bound to workspace A gets 403 on workspace B's
    tool endpoints; unbound (self-service) tokens keep the old behaviour."""
    workspace_id = _create_workspace(client)
    other_id = _create_workspace(client, "Other WS")
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    bound = client.__class__(client.app)
    bound.headers["authorization"] = (
        f"Bearer {scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)}"
    )
    assert (
        bound.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active").status_code
        == 200
    )
    response = bound.get(f"/api/studio-agent/tools/workspaces/{other_id}/workflow/active")
    assert response.status_code == 403
    assert "bound" in response.json()["detail"]


def test_get_active_revision_returns_definition_and_yaml(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "active"
    assert payload["workflow_key"] == _WORKFLOW_KEY
    assert payload["revision"]["version"] == 1
    assert payload["revision"]["status"] == "active"
    assert payload["workflow"]["key"] == _WORKFLOW_KEY
    assert payload["workflow"]["nodes"]
    assert f"key: {_WORKFLOW_KEY}" in payload["definition_yaml"]


def test_get_active_revision_empty_state_for_unpublished_workflow(client, job_db) -> None:
    """A workspace whose workflow was never published gets a structured empty
    state (200) instead of a 404, so the agent can start the from-scratch
    authoring flow."""
    scoped, _ = _scoped_client(client, job_db)
    registered = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "studio_empty_flow", "label": "Studio Empty Flow"},
    )
    assert registered.status_code == 200, registered.text
    workspace = job_db.create_workspace("ws-empty", default_workflow_key="studio_empty_flow")
    workspace_id = str(workspace["id"])

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "state": "empty",
        "workflow_key": "studio_empty_flow",
        "revision": None,
        "workflow": None,
        "definition_yaml": None,
    }


def test_get_active_revision_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/workflow/active")
    # The tools router is wrapped by secured() (routes/__init__.py), which
    # mounts require_workspace_access: non-members get 404 there so workspace
    # existence cannot be enumerated. This scoped token belongs to the admin,
    # who bypasses the membership check, so the 404 here surfaces from the
    # service layer for the unknown workspace.
    assert response.status_code == 404


def test_validate_workflow(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    registered = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "studio_validate_flow", "label": "Studio Validate Flow"},
    )
    assert registered.status_code == 200, registered.text
    workspace = job_db.create_workspace("ws-validate", default_workflow_key="studio_validate_flow")
    workspace_id = str(workspace["id"])
    url = f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/validate"
    draft_yaml = """
key: studio_validate_flow
label: Studio Validate Flow
nodes:
  publish_content:
    capability: publish_content
"""

    # No executor binding yet: the publish validation set must surface it.
    unbound = scoped.post(url, json={"definition_yaml": draft_yaml})
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["valid"] is False
    assert any("missing executor binding" in error for error in unbound.json()["errors"])

    job_db.replace_workspace_executor_configuration(
        workspace_id,
        allocations=[{"executor_id": "code-default", "concurrency_limit": 1}],
        bindings=[
            {
                "workflow_key": "studio_validate_flow",
                "node_key": "publish_content",
                "executor_id": "code-default",
            }
        ],
        node_limits=[],
    )
    valid = scoped.post(url, json={"definition_yaml": draft_yaml})
    assert valid.status_code == 200
    assert valid.json() == {"valid": True, "errors": []}

    # Parseable but structurally invalid (validate mirrors the Studio endpoint,
    # which reports definition errors rather than catching raw YAML errors).
    invalid = scoped.post(url, json={"definition_yaml": "- just\n- a\n- list\n"})
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert invalid.json()["errors"]


def test_compare_workflow_draft(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    url = f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/compare"
    active_yaml = _active_yaml(scoped, workspace_id)

    unchanged = scoped.post(url, json={"definition_yaml": active_yaml})
    assert unchanged.status_code == 200, unchanged.text
    payload = unchanged.json()
    assert payload["valid"] is True
    assert payload["creates_revision"] is False
    assert payload["summary"]["risk_level"] == "none"

    changed = scoped.post(
        url, json={"definition_yaml": active_yaml.replace("读取知识点", "读取知识点 v2")}
    )
    assert changed.status_code == 200
    changed_payload = changed.json()
    assert changed_payload["valid"] is True
    label_changes = [
        change
        for change in changed_payload["summary"]["node_changes"]
        if change["node_key"] == _NODE_KEY
    ]
    assert label_changes and "label" in label_changes[0]["fields"]

    malformed = scoped.post(url, json={"definition_yaml": "key: ["})
    assert malformed.status_code == 200
    assert malformed.json()["valid"] is False
    assert malformed.json()["errors"]


def test_list_workflow_catalog(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workflows")
    assert response.status_code == 200, response.text
    keys = {entry["key"] for entry in response.json()["workflows"]}
    assert _WORKFLOW_KEY in keys


def test_get_node_code_state_reads_builtin(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/{_NODE_KEY}/code"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    # origin=builtin is backed by the global factory seed since #96 (no path).
    assert payload["origin"] == "builtin"
    assert payload["code"]
    assert "path" not in payload
    assert payload["has_draft"] is False


def test_get_node_code_state_404_for_unknown_node(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/no_such_node/code"
    )
    assert response.status_code == 404


def test_save_node_code_draft_attributes_studio_agent(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)
    base = (
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/{_NODE_KEY}/code"
    )

    saved = scoped.put(f"{base}/draft", json={"code": _NODE_CODE, "change_note": "agent draft"})

    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["status"] == "draft"
    assert payload["created_by"] == f"studio-agent:{admin_id}"
    assert payload["code"] == _NODE_CODE

    state = scoped.get(base)
    assert state.status_code == 200
    assert state.json()["has_draft"] is True
    assert state.json()["draft_code"] == _NODE_CODE
    assert state.json()["draft_version"] == payload["version"]


def test_save_node_code_draft_rejects_invalid_code(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/{_NODE_KEY}/code/draft",
        json={"code": "def helper():\n    pass\n"},
    )
    assert response.status_code == 400
    assert "run" in response.json()["detail"]


def test_save_agent_definition_draft_attributes_studio_agent(client, job_db) -> None:
    scoped, admin_id = _scoped_client(client, job_db)

    saved = scoped.put(
        "/api/studio-agent/tools/agent-definitions/studio-test-agent/draft",
        json={
            "capability": "studio_test_capability",
            "runtime": "pi",
            "skill": "studio/test",
        },
    )

    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["agent_id"] == "studio-test-agent"
    assert payload["status"] == "draft"
    assert payload["created_by"] == f"studio-agent:{admin_id}"
    assert payload["definition"]["capability"] == "studio_test_capability"


def test_save_agent_definition_draft_rejects_invalid_payload(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.put(
        "/api/studio-agent/tools/agent-definitions/studio-test-agent/draft",
        json={"capability": "", "runtime": "pi", "skill": "studio/test"},
    )
    assert response.status_code == 422


class _RecordingWorker:
    def __init__(self) -> None:
        self.reload_calls = 0

    def reload_scan_entries(self) -> None:
        self.reload_calls += 1


def test_register_workflow_triggers_scan_reload_and_wakeup(client, job_db, monkeypatch) -> None:
    scoped, _ = _scoped_client(client, job_db)
    worker = _RecordingWorker()
    # client is the worker-session shared app: monkeypatch restores the missing
    # workflow_worker attribute after the test (raising=False: the default app
    # runs with start_worker=False and has no such attribute).
    monkeypatch.setattr(client.app.state, "workflow_worker", worker, raising=False)
    wakeups: list[int] = []
    monkeypatch.setattr(
        "server.app.routes.studio_agent_tools.notify_schedulable_work",
        lambda: wakeups.append(1),
    )

    response = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "studio_agent_flow", "label": "Studio Agent Flow"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["key"] == "studio_agent_flow"
    assert payload["origin"] == "registered"
    assert worker.reload_calls == 1
    assert wakeups

    catalog = scoped.get("/api/studio-agent/tools/workflows")
    assert "studio_agent_flow" in {entry["key"] for entry in catalog.json()["workflows"]}


def test_register_workflow_requires_admin_scoped_token(client, job_db) -> None:
    """Platform-global registration aligns with POST /api/workflows
    (require_admin): a scoped token minted for a non-admin user gets 403."""
    member_id = str(job_db.create_user("studio-member", password_hash=None)["id"])
    member_token = scoped_tokens.mint_scoped_token(job_db, member_id)
    member_scoped = client.__class__(client.app)
    member_scoped.headers["authorization"] = f"Bearer {member_token}"

    denied = member_scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "member_flow", "label": "Member Flow"},
    )
    assert denied.status_code == 403

    scoped, _ = _scoped_client(client, job_db)
    allowed = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "member_flow", "label": "Member Flow"},
    )
    assert allowed.status_code == 200, allowed.text


def test_register_workflow_conflict_and_invalid_key(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    url = "/api/studio-agent/tools/workflows/register"

    first = scoped.post(url, json={"key": "studio_conflict_flow", "label": "First"})
    assert first.status_code == 200, first.text

    conflict = scoped.post(url, json={"key": "studio_conflict_flow", "label": "Again"})
    assert conflict.status_code == 409

    invalid = scoped.post(url, json={"key": "Bad Key", "label": "Nope"})
    assert invalid.status_code == 400


def test_validate_workflow_unknown_workspace_reports_binding_errors(client, job_db) -> None:
    """validate 语义与 Studio 端点一致：未知 workspace 没有 executor 绑定，
    草稿校验失败（valid=False），而不是 404。"""
    scoped, _ = _scoped_client(client, job_db)
    draft_yaml = """
key: studio_validate_flow
label: Studio Validate Flow
nodes:
  publish_content:
    capability: publish_content
"""
    response = scoped.post(
        "/api/studio-agent/tools/workspaces/ws-missing/workflow/validate",
        json={"definition_yaml": draft_yaml},
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is False
    assert any("missing executor binding" in error for error in response.json()["errors"])


def test_compare_workflow_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.post(
        "/api/studio-agent/tools/workspaces/ws-missing/workflow/compare",
        json={"definition_yaml": "key: x\nnodes: {}\n"},
    )
    assert response.status_code == 404


def test_compare_workflow_without_baseline_returns_full_draft_preview(client, job_db) -> None:
    """Tool-surface compare on a never-published workflow (registered key, no
    revision): instead of a revision error the draft is diffed against an
    empty base, so the agent can preview the full from-scratch shape."""
    scoped, _ = _scoped_client(client, job_db)
    registered = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "studio_fresh_flow", "label": "Studio Fresh Flow"},
    )
    assert registered.status_code == 200, registered.text
    workspace = job_db.create_workspace("ws-fresh", default_workflow_key="studio_fresh_flow")
    workspace_id = str(workspace["id"])

    response = scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/compare",
        json={
            "definition_yaml": (
                "key: studio_fresh_flow\n"
                "label: Studio Fresh Flow\n"
                "nodes:\n"
                "  publish_content:\n"
                "    capability: publish_content\n"
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["base_revision"] is None
    assert payload["draft_workflow"] == {
        "key": "studio_fresh_flow",
        "label": "Studio Fresh Flow",
        "version": 0,
    }
    assert payload["creates_revision"] is True
    assert payload["summary"]["node_changes"] == [
        {
            "type": "added",
            "node_key": "publish_content",
            "label": "publish_content",
            "fields": [],
            "risk": "info",
        }
    ]
    assert any(flag["code"] == "no_baseline" for flag in payload["summary"]["risk_flags"])


def test_save_node_code_draft_404_for_unknown_node(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/no_such_node/code/draft",
        json={"code": _NODE_CODE},
    )
    assert response.status_code == 404


class _FailingReloadWorker:
    def reload_scan_entries(self) -> None:
        raise RuntimeError("catalog read failed")


def test_register_workflow_reload_failure_keeps_committed_write(
    client, job_db, monkeypatch
) -> None:
    """注册提交后热刷新失败的半应用语义：catalog 行已提交，路由不得 500，
    由 poll loop 的周期对账收敛扫描表。"""
    scoped, _ = _scoped_client(client, job_db)
    monkeypatch.setattr(client.app.state, "workflow_worker", _FailingReloadWorker(), raising=False)

    response = scoped.post(
        "/api/studio-agent/tools/workflows/register",
        json={"key": "reload_failure_flow", "label": "Reload Failure Flow"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"] == "reload_failure_flow"
    catalog = scoped.get("/api/studio-agent/tools/workflows")
    assert "reload_failure_flow" in {entry["key"] for entry in catalog.json()["workflows"]}
