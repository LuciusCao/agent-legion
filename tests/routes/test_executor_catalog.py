def test_executor_catalog_contains_only_host_local_executor(client_factory):
    with client_factory() as client:
        response = client.get("/api/executors")

    assert response.status_code == 200
    executors = response.json()["executors"]
    assert [executor["id"] for executor in executors] == ["local-default"]
