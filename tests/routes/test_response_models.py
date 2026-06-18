def test_core_api_routes_declare_response_models(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths["/api/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/HealthResponse"}
    assert paths["/api/agents"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/AgentsResponse"}
    assert paths["/api/videos/batch/delete"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchDeleteResponse"}
    assert paths["/api/videos/batch/rerun"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchRerunResponse"}
    assert paths["/api/package"]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PackageResponse"}
    assert paths["/api/worker/status"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/WorkerStatusResponse"}


def test_run_to_routes_declare_response_models(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths["/api/videos/{video_id}/run-to"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/RunToSingleResponse"}
    assert paths["/api/videos/batch/run-to"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/BatchRunToResponse"}
