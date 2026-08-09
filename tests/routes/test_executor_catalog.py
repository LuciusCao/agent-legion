def test_executor_catalog_contains_only_host_executors(client_factory):
    with client_factory() as client:
        response = client.get("/api/executors")

    assert response.status_code == 200
    executors = response.json()["executors"]
    assert [executor["id"] for executor in executors] == ["code-default"]


def test_executor_catalog_reflects_published_edits(client_factory):
    """Editing the DB published definition via the management API shows in the catalog."""
    edited = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"clean_and_parse": {"path": "workflow_nodes/question_clean_parse.py"}},
    }
    with client_factory() as client:
        assert (
            client.put("/api/executor-definitions/code-default/draft", json=edited).status_code
            == 200
        )
        assert client.post("/api/executor-definitions/code-default/publish").status_code == 200
        response = client.get("/api/executors")

    assert response.status_code == 200
    executors = {executor["id"]: executor for executor in response.json()["executors"]}
    assert executors["code-default"]["global_capacity"] == 4
    assert executors["code-default"]["capabilities"] == ["clean_and_parse"]
