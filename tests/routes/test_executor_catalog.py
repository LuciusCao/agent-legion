from server.app.executors.config import RemoteCapabilityConfig, RemoteExecutorConfig


def test_executor_catalog_serializes_remote_executor_definition(client_factory):
    def configure(app):
        app.state.settings.executor_definitions["pi-remote"] = RemoteExecutorConfig(
            kind="remote",
            global_capacity=8,
            capabilities={
                "generate_key_info": RemoteCapabilityConfig(
                    skill="question_comprehension_info/generate_key_info"
                )
            },
        )

    with client_factory(configure=configure) as client:
        response = client.get("/api/executors")

    assert response.status_code == 200
    executors = {executor["id"]: executor for executor in response.json()["executors"]}
    remote = executors["pi-remote"]
    assert remote["kind"] == "remote"
    assert remote["global_capacity"] == 8
    assert remote["capabilities"] == ["generate_key_info"]
