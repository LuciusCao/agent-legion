import json

from scripts.export_openapi import build_openapi_schema


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
    assert "WorkspaceSettingsResponse" in first["components"]["schemas"]
    assert str(tmp_path) not in json.dumps(first, sort_keys=True)
