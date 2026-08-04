import pytest


@pytest.mark.parametrize(
    ("path", "method", "schema_name"),
    [
        (
            "/api/workspaces/{workspace_id}/packages",
            "get",
            "WorkspacePackagesResponse",
        ),
    ],
)
def test_package_routes_use_named_response_contracts(
    client, path: str, method: str, schema_name: str
) -> None:
    operation = client.app.openapi()["paths"][path][method]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert schema == {"$ref": f"#/components/schemas/{schema_name}"}
