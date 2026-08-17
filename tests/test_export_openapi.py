import json

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse

from scripts import export_openapi
from scripts.export_openapi import build_openapi_schema, validate_response_contracts


def test_build_openapi_schema_is_deterministic_and_portable(tmp_path):
    first = build_openapi_schema(tmp_path / "first")
    second = build_openapi_schema(tmp_path / "second")

    assert first == second
    assert first["info"]["title"] == "Agent Legion"
    paths = first["paths"]
    assert paths
    assert all(path.startswith("/api") for path in paths)
    assert "/" not in paths
    assert "/{path}" not in paths
    assert "/{path:path}" not in paths
    assert "/api/workspaces/{workspace_id}/settings" in paths
    schemas = first["components"]["schemas"]
    assert "WorkspaceSettingsResponse" in schemas
    assert "ExecutorCatalogResponse" in schemas
    assert "WorkspaceExecutorConfigurationResponse" in schemas
    workspace_config = schemas["WorkspaceConfigurationRequest"]
    workspace_config_props = workspace_config.get("properties", {})
    # P-0.5: allocations/bindings retired with the executor concept (v47).
    assert "executor_allocations" not in workspace_config_props
    assert "node_bindings" not in workspace_config_props
    assert "node_limits" in workspace_config_props
    assert "agents" not in workspace_config_props
    assert str(tmp_path) not in json.dumps(first, sort_keys=True)


def test_validate_response_contracts_rejects_inline_json_schema():
    schema = {
        "paths": {
            "/api/example": {
                "get": {
                    "operationId": "example_api_example_get",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            }
                        }
                    },
                }
            }
        }
    }

    with pytest.raises(ValueError, match="example_api_example_get"):
        validate_response_contracts(schema, exempt_operation_ids=set())


def test_validate_response_contracts_accepts_refs_and_exemptions():
    schema = {
        "paths": {
            "/api/model": {
                "get": {
                    "operationId": "model_api_model_get",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ModelResponse"}
                                }
                            }
                        }
                    },
                }
            },
            "/api/legacy": {
                "get": {
                    "operationId": "legacy_api_legacy_get",
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            },
        }
    }

    validate_response_contracts(schema, exempt_operation_ids={"legacy_api_legacy_get"})


def test_validate_response_contracts_matches_exact_operation_id():
    schema = {
        "paths": {
            "/api/protocol": {
                "get": {
                    "operationId": "shared_api_protocol_get",
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            },
            "/api/json": {
                "get": {
                    "operationId": "shared_api_json_get",
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            },
        }
    }

    with pytest.raises(ValueError) as exc_info:
        validate_response_contracts(schema, exempt_operation_ids={"shared_api_protocol_get"})

    assert "shared_api_protocol_get" not in str(exc_info.value)
    assert "shared_api_json_get" in str(exc_info.value)


def test_protocol_operation_ids_distinguish_duplicate_endpoint_names():
    app = FastAPI()

    def shared() -> FileResponse:
        raise NotImplementedError

    app.get("/protocol", response_class=FileResponse)(shared)

    def shared() -> dict[str, str]:
        return {}

    app.get("/json")(shared)
    protocol_route = next(
        route for route in app.routes if getattr(route, "path", "") == "/protocol"
    )
    json_route = next(route for route in app.routes if getattr(route, "path", "") == "/json")

    operation_ids = export_openapi.response_contract_exempt_operation_ids(app, set())

    assert protocol_route.unique_id in operation_ids
    assert json_route.unique_id not in operation_ids


def test_legacy_exemption_matches_endpoint_source_file_and_name():
    app = FastAPI()

    def endpoint_from(relative_path: str):
        namespace: dict[str, object] = {}
        filename = export_openapi.PROJECT_ROOT / relative_path
        exec(
            compile("def shared() -> dict[str, str]:\n    return {}\n", str(filename), "exec"),
            namespace,
        )
        return namespace["shared"]

    legacy = endpoint_from("server/app/routes/legacy.py")
    unrelated = endpoint_from("server/app/routes/unrelated.py")
    app.get("/legacy")(legacy)
    app.get("/unrelated")(unrelated)
    legacy_route = next(route for route in app.routes if getattr(route, "path", "") == "/legacy")
    unrelated_route = next(
        route for route in app.routes if getattr(route, "path", "") == "/unrelated"
    )

    operation_ids = export_openapi.response_contract_exempt_operation_ids(
        app, {"server/app/routes/legacy.py:shared"}
    )

    assert legacy_route.unique_id in operation_ids
    assert unrelated_route.unique_id not in operation_ids
