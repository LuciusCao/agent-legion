def test_list_executors_endpoint(client):
    response = client.get("/api/executors")
    assert response.status_code == 200
    data = response.json()
    executor = data["executors"][0]
    assert executor["id"] == "local-default"
    assert executor["kind"] == "local"
    assert executor["global_capacity"] == 128
    assert executor["capabilities"] == [
        "assemble_comprehension_info",
        "assemble_video_metadata",
        "classify_comprehension_eligibility",
        "clean_and_parse",
        "download_video",
        "fetch_questions",
        "finalize_non_uploadable",
        "package_video_job",
        "transcribe_video",
    ]
    assert {
        "name": "fetch_questions",
        "handler": "question_comprehension_info.fetch_questions",
        "skill": None,
        "tools": [],
        "provider": None,
        "model": None,
        "thinking": None,
        "skill_ref": None,
        "skill_commit": None,
    } in executor["capability_details"]


def test_get_configured_skill_detail(client):
    response = client.get("/api/executors/skills/question_comprehension_info/generate_key_info")

    assert response.status_code == 200
    data = response.json()
    assert data["ref"] == "v1.3.8"
    assert data["commit"].startswith("5c5eae7")
    assert any(item["path"] == "SKILL.md" for item in data["files"])


def test_get_workspace_executor_configuration_reports_no_warnings_after_v005(client):
    workspace_response = client.post(
        "/api/workspaces",
        json={"name": "Legacy", "default_workflow_key": "question_comprehension_info"},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # The legacy workspace_agent_assignments table is removed by V005, so no
    # migration warnings are produced.
    response = client.get(f"/api/workspaces/{workspace_id}/executor-configuration")
    assert response.status_code == 200
    data = response.json()
    assert data["allocations"] == []
    assert data["bindings"] == []
    assert data["node_limits"] == []
    assert data["migration_warnings"] == []
