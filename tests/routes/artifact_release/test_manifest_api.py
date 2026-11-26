from __future__ import annotations

_WORKSPACE_ID = "release-api"
_URL = f"/api/workspaces/{_WORKSPACE_ID}/release-manifest"


def _create_workspace(client) -> None:
    response = client.post("/api/workspaces", json={"id": _WORKSPACE_ID, "name": "Release"})
    assert response.status_code == 200, response.text


def _create_user(client, username: str, role: str | None):
    response = client.post("/api/users", json={"username": username, "password": "pw1"})
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    if role is not None:
        response = client.put(
            f"/api/workspaces/{_WORKSPACE_ID}/members",
            json={"user_id": user_id, "role": role},
        )
        assert response.status_code == 200, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": "pw1"})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def test_release_manifest_api_roundtrip_conflict_and_openapi(client) -> None:
    assert client.get(_URL).status_code == 404
    assert client.get(f"{_URL}/revisions").status_code == 404
    assert (
        client.put(
            _URL, json={"artifact_names": ["result.json"], "expected_revision": 0}
        ).status_code
        == 404
    )
    _create_workspace(client)

    response = client.get(_URL)
    assert response.status_code == 200, response.text
    empty = response.json()
    assert empty["revision"] == 0
    assert empty["artifact_names"] == []
    assert empty["created_at"] is None

    response = client.put(
        _URL,
        json={
            "artifact_names": ["result.json", "reports/final.json"],
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["revision"] == 1
    assert first["artifact_names"] == ["reports/final.json", "result.json"]

    response = client.put(_URL, json={"artifact_names": ["result.json"], "expected_revision": 1})
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["revision"] == 2
    assert current["artifact_names"] == ["result.json"]
    assert current["manifest_hash"] != first["manifest_hash"]

    response = client.put(_URL, json={"artifact_names": ["stale.json"], "expected_revision": 1})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["current_manifest"] == current

    response = client.get(f"{_URL}/revisions")
    assert response.status_code == 200, response.text
    assert [row["revision"] for row in response.json()["revisions"]] == [2, 1]

    assert (
        client.put(
            _URL, json={"artifact_names": ["../escape.json"], "expected_revision": 2}
        ).status_code
        == 400
    )
    assert client.put(_URL, json={"artifact_names": []}).status_code == 422
    assert (
        client.put(
            _URL,
            json={"artifact_names": [], "expected_revision": 2, "unexpected": True},
        ).status_code
        == 422
    )
    assert client.get(_URL).json() == current

    schema = client.app.openapi()
    assert set(schema["paths"]["/api/workspaces/{workspace_id}/release-manifest"]) >= {
        "get",
        "put",
    }
    assert "get" in schema["paths"]["/api/workspaces/{workspace_id}/release-manifest/revisions"]
    record = schema["components"]["schemas"]["WorkspaceReleaseManifestRecord"]["properties"]
    assert {"revision", "artifact_names", "manifest_hash", "created_by"} <= set(record)


def test_release_manifest_requires_authentication(anon_client) -> None:
    assert anon_client.get(_URL).status_code == 401
    assert anon_client.get(f"{_URL}/revisions").status_code == 401
    assert (
        anon_client.put(_URL, json={"artifact_names": [], "expected_revision": 0}).status_code
        == 401
    )


def test_release_manifest_viewer_editor_outsider_and_scoped_token(client) -> None:
    _create_workspace(client)
    viewer = _create_user(client, "release-viewer", "viewer")
    outsider = _create_user(client, "release-outsider", None)
    editor = _create_user(client, "release-editor", "editor")
    payload = {"artifact_names": ["result.json"], "expected_revision": 0}

    assert viewer.get(_URL).status_code == 200
    assert viewer.get(f"{_URL}/revisions").status_code == 200
    assert viewer.put(_URL, json=payload).status_code == 403
    assert outsider.get(_URL).status_code == 404
    assert outsider.put(_URL, json=payload).status_code == 404
    assert client.get(_URL).json()["revision"] == 0

    from server.app.auth import scoped_tokens

    admin_id = client.app.state.job_db.get_user_credentials("admin")["id"]
    scoped = client.__class__(client.app)
    token = scoped_tokens.mint_scoped_token(client.app.state.job_db, admin_id)
    scoped.headers["authorization"] = f"Bearer {token}"
    assert scoped.put(_URL, json=payload).status_code == 403

    response = editor.put(_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert client.put(_URL, json={**payload, "expected_revision": 1}).status_code == 200
