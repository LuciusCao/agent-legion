def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": "catalog-ws", "default_workflow_key": "education_video_problems_generation"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def test_executor_catalog_contains_only_host_executors(client_factory):
    with client_factory() as client:
        workspace_id = _create_workspace(client)
        response = client.get("/api/executors", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    executors = response.json()["executors"]
    assert [executor["id"] for executor in executors] == ["code-default"]


def test_executor_catalog_reflects_published_edits(client_factory):
    """Editing the DB published definition via the management API shows in the catalog."""
    # fresh=True: publish hot-reloads the app's in-memory executor registry;
    # on the worker-session shared app that mutation would leak into later
    # tests (the per-test reset only restores DB state, not app.state).
    edited = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"publish_content": {"path": "workflow_nodes/example_publish.py"}},
    }
    with client_factory(fresh=True) as client:
        workspace_id = _create_workspace(client)
        assert (
            client.put("/api/executor-definitions/code-default/draft", json=edited).status_code
            == 200
        )
        assert client.post("/api/executor-definitions/code-default/publish").status_code == 200
        response = client.get("/api/executors", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    executors = {executor["id"]: executor for executor in response.json()["executors"]}
    assert executors["code-default"]["global_capacity"] == 4
    assert executors["code-default"]["capabilities"] == ["publish_content"]
