"""POST /api/workspaces/{id}/workflow/node-prompt-preview（节点运行 prompt 预览）。"""

from __future__ import annotations

from tests.helpers import publish_builtin_revision

_WORKFLOW_KEY = "education_video_problems_generation"


def _url(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/workflow/node-prompt-preview"


def _workspace(job_db, name: str) -> str:
    workspace = job_db.create_workspace(name, default_workflow_key=_WORKFLOW_KEY)
    publish_builtin_revision(job_db, str(workspace["id"]))
    return str(workspace["id"])


def test_preview_active_revision_node(client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-preview")

    response = client.post(_url(workspace_id), json={"node_key": "write_script"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["is_default"] is True
    assert payload["custom_instructions"] == ""
    assert payload["skill_key"] == "education-video-problems-generation/write-script"
    assert "撰写教学视频脚本" in payload["default_instructions"]
    assert "Job ID: <job_id>" in payload["effective_prompt"]
    assert "Working directory: {job_dir}" in payload["effective_prompt"]
    assert payload["default_instructions"] in payload["effective_prompt"]


def test_preview_definition_yaml_override(client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-yaml")
    draft_yaml = (
        "key: wf\nlabel: Draft\nnodes:\n"
        "  gen:\n"
        "    label: 生成\n"
        "    capability: write_script\n"
        "    outputs: [out.json]\n"
        "    execution:\n"
        "      prompt: House style only.\n"
    )

    response = client.post(
        _url(workspace_id), json={"node_key": "gen", "definition_yaml": draft_yaml}
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["is_default"] is False
    assert payload["custom_instructions"] == "House style only."
    assert "Node instructions:\nHouse style only." in payload["effective_prompt"]
    assert payload["default_instructions"] not in payload["effective_prompt"]


def test_preview_unknown_node_gets_404(client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-404")

    response = client.post(_url(workspace_id), json={"node_key": "no_such_node"})

    assert response.status_code == 404


def test_preview_start_node_gets_400(client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-start")

    response = client.post(_url(workspace_id), json={"node_key": "_start"})

    assert response.status_code == 400


def test_preview_unknown_workspace_gets_404(client) -> None:
    response = client.post(_url("ws-np-missing"), json={"node_key": "gen"})
    assert response.status_code == 404


def test_preview_without_active_revision_gets_404(client, job_db) -> None:
    job_db.create_workspace("ws-np-bare", default_workflow_key="bare_flow")

    response = client.post(_url("ws-np-bare"), json={"node_key": "gen"})

    assert response.status_code == 404


def test_preview_invalid_yaml_gets_400(client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-invalid")

    response = client.post(
        _url(workspace_id), json={"node_key": "gen", "definition_yaml": "- just\n- a\n- list\n"}
    )

    assert response.status_code == 400


def test_anonymous_gets_401(anon_client, job_db) -> None:
    workspace_id = _workspace(job_db, "ws-np-anon")

    response = anon_client.post(_url(workspace_id), json={"node_key": "write_script"})

    assert response.status_code == 401
