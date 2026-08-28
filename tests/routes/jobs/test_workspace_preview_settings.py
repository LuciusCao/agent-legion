"""Workspace preview settings section (schema v63 preview_config_json)."""

from tests.helpers import publish_builtin_revision


def _create_workspace(client, name="default"):
    ws_id = client.post("/api/workspaces", json={"id": name, "name": name}).json()["workspace"][
        "id"
    ]
    publish_builtin_revision(client.app.state.job_db, ws_id)
    return ws_id


def test_settings_payload_defaults_to_empty_preview_hidden(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    assert response.json()["settings"]["previewHidden"] == []


def test_patch_preview_section_round_trips(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        saved = c.patch(
            f"/api/workspaces/{ws_id}/settings/preview",
            json={"previewHidden": ["questions.json", "comprehension_info.json"]},
        )
        fetched = c.get(f"/api/workspaces/{ws_id}/settings")

    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["previewHidden"] == [
        "comprehension_info.json",
        "questions.json",
    ]
    assert fetched.json()["settings"]["previewHidden"] == [
        "comprehension_info.json",
        "questions.json",
    ]


def test_patch_preview_section_requires_payload(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.patch(f"/api/workspaces/{ws_id}/settings/preview", json={})

    assert response.status_code == 400


def test_patch_preview_section_rejects_non_string_entries(client_factory):
    # pydantic list[str] 注解在契约层即拒绝（422），早于 service 层校验。
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.patch(
            f"/api/workspaces/{ws_id}/settings/preview",
            json={"previewHidden": ["ok.json", 42]},
        )

    assert response.status_code == 422


def test_patch_preview_section_unknown_workspace_is_404(client_factory):
    with client_factory(workflows_enabled=True) as c:
        response = c.patch(
            "/api/workspaces/missing/settings/preview",
            json={"previewHidden": ["a.json"]},
        )

    assert response.status_code == 404


def test_put_configuration_without_preview_keeps_saved_hidden(client_factory):
    """PUT 全量保存缺省 previewHidden = 未改：不抹掉已有勾选（旧客户端兼容）。"""
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        patched = c.patch(
            f"/api/workspaces/{ws_id}/settings/preview",
            json={"previewHidden": ["questions.json"]},
        )
        assert patched.status_code == 200

        put = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={"name": "Renamed", "settings": {}},
        )
        fetched = c.get(f"/api/workspaces/{ws_id}/settings")

    assert put.status_code == 200, put.text
    assert put.json()["settings"]["previewHidden"] == ["questions.json"]
    assert fetched.json()["settings"]["previewHidden"] == ["questions.json"]


def test_put_configuration_with_preview_overwrites_hidden(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        patched = c.patch(
            f"/api/workspaces/{ws_id}/settings/preview",
            json={"previewHidden": ["old.json", "questions.json"]},
        )
        assert patched.status_code == 200

        put = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={"name": "Renamed", "settings": {"previewHidden": ["new.json"]}},
        )

    assert put.status_code == 200, put.text
    assert put.json()["settings"]["previewHidden"] == ["new.json"]
