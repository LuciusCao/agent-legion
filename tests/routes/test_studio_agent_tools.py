"""Behavioral contract for the studio-agent tool surface (/api/studio-agent/tools/*).

Every tool endpoint requires a studio-agent scoped token (STUDIO-AGENT-001):
anonymous callers get 401 and full user sessions get 403, while scoped
tokens pass the scope guard (business errors may still apply). Draft writes
are attributed with ``created_by=f"studio-agent:{user_id}"``. The endpoint
inventory in ``_tool_endpoints`` is the enumeration backstop: new tool
endpoints must be added here.
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
        ("PUT", f"{base}/agent-definitions/agent-x/draft", {}),
        ("GET", f"{base}/workflow/active", None),
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

    # No published node code yet: the publish validation set must surface it.
    unbound = scoped.post(url, json={"definition_yaml": draft_yaml})
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["valid"] is False
    assert any("no published node code" in error for error in unbound.json()["errors"])

    from server.app.services.node_codes import NodeCodeService

    codes = NodeCodeService(job_db.path)
    codes.save_draft(
        workspace_id,
        "studio_validate_flow",
        "publish_content",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, "studio_validate_flow", "publish_content")
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


def test_get_node_code_state_reads_builtin(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/{_NODE_KEY}/code"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    # origin=builtin is backed by a system-seeded workspace version (no path).
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


def test_node_code_tools_404_for_start_node(client, job_db) -> None:
    # The injected `_start` entry node never executes: reading its code or
    # saving a draft for it gets the same 404 as an unknown node.
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    base = (
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/_start/code"
    )
    assert scoped.get(base).status_code == 404
    assert scoped.put(f"{base}/draft", json={"code": _NODE_CODE}).status_code == 404


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
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)

    saved = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        "/agent-definitions/studio-test-agent/draft",
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
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        "/agent-definitions/studio-test-agent/draft",
        json={"capability": "", "runtime": "pi", "skill": "studio/test"},
    )
    assert response.status_code == 422


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
    assert any("no published node code" in error for error in response.json()["errors"])


def test_compare_workflow_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.post(
        "/api/studio-agent/tools/workspaces/ws-missing/workflow/compare",
        json={"definition_yaml": "key: x\nnodes: {}\n"},
    )
    assert response.status_code == 404


def test_compare_workflow_without_baseline_returns_full_draft_preview(client, job_db) -> None:
    """Tool-surface compare on a never-published workflow (workspace key set,
    no revision): instead of a revision error the draft is diffed against an
    empty base, so the agent can preview the full from-scratch shape."""
    scoped, _ = _scoped_client(client, job_db)
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
            # 草稿未声明 start：loader 注入合成 start（EXEC-WORKFLOW-START-001），
            # 无基线对比下它也作为新增节点出现。
            "type": "added",
            "node_key": "_start",
            "label": "Start",
            "fields": [],
            "risk": "info",
        },
        {
            "type": "added",
            "node_key": "publish_content",
            "label": "publish_content",
            "fields": [],
            "risk": "info",
        },
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


def test_save_node_code_draft_expected_capability_match_and_mismatch(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)
    url = (
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/{_NODE_KEY}/code/draft"
    )

    matched = scoped.put(
        url,
        json={"code": _NODE_CODE, "expected_capability": "intake_knowledge_points"},
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["status"] == "draft"

    mismatched = scoped.put(
        url,
        json={"code": _NODE_CODE, "expected_capability": "some_other_capability"},
    )
    assert mismatched.status_code == 400
    detail = mismatched.json()["detail"]
    assert "intake_knowledge_points" in detail
    assert "some_other_capability" in detail


def test_save_node_code_draft_expected_capability_allows_new_node(client, job_db) -> None:
    """A node key absent from the active revision gets a skeleton draft when
    expected_capability declares the intent (the workflow draft introducing
    the node is published later by the human)."""
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)

    saved = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        f"/workflows/{_WORKFLOW_KEY}/nodes/brand_new_node/code/draft",
        json={"code": _NODE_CODE, "expected_capability": "brand_new_capability"},
    )

    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["status"] == "draft"
    assert payload["created_by"] == f"studio-agent:{admin_id}"


def test_save_node_code_draft_skeleton_without_any_revision(client, job_db) -> None:
    """From-scratch flow: no active revision at all. expected_capability gates
    the skeleton draft; without it the historic 404 stands."""
    scoped, _ = _scoped_client(client, job_db)
    workspace = job_db.create_workspace("ws-skeleton", default_workflow_key="studio_skeleton_flow")
    workspace_id = str(workspace["id"])
    url = (
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        "/workflows/studio_skeleton_flow/nodes/first_node/code/draft"
    )

    rejected = scoped.put(url, json={"code": _NODE_CODE})
    assert rejected.status_code == 404

    saved = scoped.put(url, json={"code": _NODE_CODE, "expected_capability": "first_capability"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "draft"
