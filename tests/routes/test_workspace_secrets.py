"""Workspace secrets API: write-only contract, vault diversion, auth matrix."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.helpers.auth import authenticate_client

PLAINTEXT = "super-secret-token-value"


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.fixture
def app(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return app


@pytest.fixture
def admin_client(app):
    with authenticate_client(TestClient(app)) as client:
        yield client


def _create_workspace(client: TestClient) -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": "secrets-ws", "default_workflow_key": "question_comprehension_info"},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


def test_secret_put_get_list_delete_roundtrip(admin_client, vault_key):
    workspace_id = _create_workspace(admin_client)

    put = admin_client.put(
        f"/api/workspaces/{workspace_id}/secrets/api-token",
        json={"value": PLAINTEXT},
    )
    assert put.status_code == 200, put.text
    metadata = put.json()["secret"]
    assert metadata["name"] == "api-token"
    assert metadata["created_at"]
    assert metadata["updated_at"]
    assert PLAINTEXT not in put.text

    listed = admin_client.get(f"/api/workspaces/{workspace_id}/secrets")
    assert listed.status_code == 200
    names = [entry["name"] for entry in listed.json()["secrets"]]
    assert names == ["api-token"]
    for entry in listed.json()["secrets"]:
        assert set(entry) == {"name", "created_at", "updated_at"}
    assert PLAINTEXT not in listed.text

    deleted = admin_client.delete(f"/api/workspaces/{workspace_id}/secrets/api-token")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "api-token"}
    assert admin_client.get(f"/api/workspaces/{workspace_id}/secrets").json() == {"secrets": []}


def test_secret_put_requires_master_key(admin_client, monkeypatch):
    # Patch the resolver instead of deleting env vars: the app under test loads
    # .env at startup, so a real key file may already be mapped into config.
    monkeypatch.setattr(
        "server.app.services.vault.resolve_master_key", lambda *_args, **_kwargs: None
    )
    workspace_id = _create_workspace(admin_client)

    response = admin_client.put(
        f"/api/workspaces/{workspace_id}/secrets/api-token",
        json={"value": PLAINTEXT},
    )

    assert response.status_code == 400
    assert "AGENT_LEGION_VAULT_MASTER_KEY" in response.json()["detail"]


def test_secret_put_rejects_empty_value(admin_client, vault_key):
    workspace_id = _create_workspace(admin_client)
    response = admin_client.put(
        f"/api/workspaces/{workspace_id}/secrets/api-token", json={"value": ""}
    )
    assert response.status_code == 422


def test_secrets_unknown_workspace_returns_404(admin_client, vault_key):
    assert admin_client.get("/api/workspaces/nope/secrets").status_code == 404
    assert (
        admin_client.put("/api/workspaces/nope/secrets/a", json={"value": "v"}).status_code == 404
    )
    assert admin_client.delete("/api/workspaces/nope/secrets/a").status_code == 404


def test_secrets_endpoints_require_auth(anon_client):
    assert anon_client.get("/api/workspaces/ws/secrets").status_code == 401
    assert anon_client.put("/api/workspaces/ws/secrets/a", json={"value": "v"}).status_code == 401
    assert anon_client.delete("/api/workspaces/ws/secrets/a").status_code == 401


def test_secrets_non_member_gets_404(admin_client, vault_key):
    workspace_id = _create_workspace(admin_client)
    created = admin_client.post("/api/users", json={"username": "member1", "password": "pw1"})
    assert created.status_code == 201, created.text
    member = TestClient(admin_client.app)
    login = member.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    assert login.status_code == 200
    member.headers["x-agent-legion-request"] = "1"

    assert member.get(f"/api/workspaces/{workspace_id}/secrets").status_code == 404
    assert (
        member.put(f"/api/workspaces/{workspace_id}/secrets/a", json={"value": "v"}).status_code
        == 404
    )


def test_node_config_secret_saved_to_vault_not_settings(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)

    saved = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={
            "nodeConfig": {
                "fetch_questions": {
                    "api_url": "http://cms.example.com/question/detail",
                    "token": PLAINTEXT,
                }
            }
        },
    )
    assert saved.status_code == 200, saved.text
    assert PLAINTEXT not in saved.text

    fetched = admin_client.get(f"/api/workspaces/{workspace_id}/settings")
    assert fetched.status_code == 200
    assert PLAINTEXT not in fetched.text
    config = fetched.json()["settings"]["nodeConfig"]["fetch_questions"]
    assert config["token"] == {"secret_set": True}
    assert config["api_url"] == "http://cms.example.com/question/detail"

    # Persistence holds only the secret_ref marker; plaintext lives in the vault.
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["question_comprehension_info"]["fetch_questions"]
    assert stored["token"] == {
        "secret_ref": "node:question_comprehension_info:fetch_questions:token"
    }
    assert PLAINTEXT not in str(workspace["node_config"])
    listed = admin_client.get(f"/api/workspaces/{workspace_id}/secrets")
    assert [entry["name"] for entry in listed.json()["secrets"]] == [
        "node:question_comprehension_info:fetch_questions:token"
    ]


def test_node_config_resave_without_secret_keeps_ref(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    patch = {"nodeConfig": {"fetch_questions": {"token": PLAINTEXT}}}
    assert (
        admin_client.patch(f"/api/workspaces/{workspace_id}/settings/nodes", json=patch).status_code
        == 200
    )

    resaved = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={"nodeConfig": {"fetch_questions": {"api_url": "http://cms.example.com/other"}}},
    )
    assert resaved.status_code == 200, resaved.text
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["question_comprehension_info"]["fetch_questions"]
    assert stored["token"] == {
        "secret_ref": "node:question_comprehension_info:fetch_questions:token"
    }


def test_node_config_masked_echo_keeps_stored_ref(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    assert (
        admin_client.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"token": PLAINTEXT}}},
        ).status_code
        == 200
    )
    masked = admin_client.get(f"/api/workspaces/{workspace_id}/settings").json()["settings"][
        "nodeConfig"
    ]

    resaved = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={"nodeConfig": masked},
    )
    assert resaved.status_code == 200, resaved.text
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["question_comprehension_info"]["fetch_questions"]
    assert stored["token"] == {
        "secret_ref": "node:question_comprehension_info:fetch_questions:token"
    }


def test_node_config_empty_secret_clears_vault_entry(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    assert (
        admin_client.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={
                "nodeConfig": {
                    "fetch_questions": {
                        "api_url": "http://cms.example.com/question/detail",
                        "token": PLAINTEXT,
                    }
                }
            },
        ).status_code
        == 200
    )

    cleared = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={
            "nodeConfig": {
                "fetch_questions": {
                    "api_url": "http://cms.example.com/question/detail",
                    "token": "",
                }
            }
        },
    )
    assert cleared.status_code == 200, cleared.text
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["question_comprehension_info"]["fetch_questions"]
    assert "token" not in stored
    assert stored["api_url"] == "http://cms.example.com/question/detail"
    listed = admin_client.get(f"/api/workspaces/{workspace_id}/secrets")
    assert listed.json() == {"secrets": []}
    config = cleared.json()["settings"]["nodeConfig"]["fetch_questions"]
    assert config["token"] == {"secret_set": False}


def test_node_config_save_without_master_key_fails(admin_client, monkeypatch):
    # See test_secret_put_requires_master_key: patch the resolver, not env.
    monkeypatch.setattr(
        "server.app.services.vault.resolve_master_key", lambda *_args, **_kwargs: None
    )
    workspace_id = _create_workspace(admin_client)

    response = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={"nodeConfig": {"fetch_questions": {"token": PLAINTEXT}}},
    )

    assert response.status_code == 400
    assert "AGENT_LEGION_VAULT_MASTER_KEY" in response.json()["detail"]
