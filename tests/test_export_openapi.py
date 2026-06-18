import json

import pytest

from scripts.export_openapi import build_openapi_schema, validate_response_contracts


def test_build_openapi_schema_is_deterministic_and_portable(tmp_path):
    first = build_openapi_schema(tmp_path / "first")
    second = build_openapi_schema(tmp_path / "second")

    assert first == second
    assert first["info"]["title"] == "Video Hive"
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
    assert "executor_allocations" in workspace_config_props
    assert "node_bindings" in workspace_config_props
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
        validate_response_contracts(schema, exempt_operation_names=set())


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

    validate_response_contracts(schema, exempt_operation_names={"legacy"})
