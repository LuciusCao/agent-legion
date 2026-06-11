import json

from scripts.export_openapi import build_openapi_schema


def test_build_openapi_schema_is_deterministic_and_portable(tmp_path):
    first = build_openapi_schema(tmp_path)
    second = build_openapi_schema(tmp_path)

    assert first == second
    assert first["info"]["title"] == "Video Hive"
    assert "/api/workspaces/{workspace_id}/settings" in first["paths"]
    assert "WorkspaceSettingsResponse" in first["components"]["schemas"]
    assert str(tmp_path) not in json.dumps(first, sort_keys=True)
