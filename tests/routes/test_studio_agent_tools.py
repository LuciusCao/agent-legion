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
_WORKFLOW_KEY = "question_comprehension_info"
_NODE_KEY = "clean_and_parse"


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
        assert response.status_code not in (401, 403), (
            f"{method} {url} -> {response.status_code}: {response.text}"
        )


def test_get_active_revision_returns_definition_and_yaml(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"]["version"] == 1
    assert payload["revision"]["status"] == "active"
    assert payload["workflow"]["key"] == _WORKFLOW_KEY
    assert payload["workflow"]["nodes"]
    assert f"key: {_WORKFLOW_KEY}" in payload["definition_yaml"]


def test_get_active_revision_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/workflow/active")
    # require_workspace_access hides unknown workspaces from non-members, but
    # the scoped token belongs to the admin, so the service-level 404 shows.
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
  clean_and_parse:
    capability: clean_and_parse
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
                "node_key": "clean_and_parse",
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
        url, json={"definition_yaml": active_yaml.replace("清洗与解析", "清洗与解析 v2")}
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
    assert payload["origin"] == "builtin"
    assert payload["code"]
    assert payload["path"].endswith(".py")
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
    client.app.state.workflow_worker = worker
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


def test_register_workflow_conflict_and_invalid_key(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    url = "/api/studio-agent/tools/workflows/register"

    first = scoped.post(url, json={"key": "studio_conflict_flow", "label": "First"})
    assert first.status_code == 200, first.text

    conflict = scoped.post(url, json={"key": "studio_conflict_flow", "label": "Again"})
    assert conflict.status_code == 409

    invalid = scoped.post(url, json={"key": "Bad Key", "label": "Nope"})
    assert invalid.status_code == 400
