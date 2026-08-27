"""Workspace secrets API: write-only contract, vault diversion, auth matrix."""

from __future__ import annotations

import tempfile

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from server.app.agent_catalog import AgentDefinition
from server.app.main import create_app
from server.app.services.agent_service import AgentService
from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL

PLAINTEXT = "super-secret-token-value"


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


def _publish_secret_node_schema(workspace_id: str) -> None:
    """Publish a test agent version whose capability schema declares a secret field.

    The demo nodes declare no secret fields, so the generic node-config vault
    diversion mechanism is exercised through a republished write_script agent
    declaring a ``secret: true`` field. Agent definitions are workspace-scoped
    (schema v46); creation seeds nothing since schema v61, so this helper
    first publishes the demo revision + factory agents, then publishes the
    secret-carrying write_script v2 inside that workspace.
    """
    from pathlib import Path

    from server.app.jobs import JobQueries
    from tests.helpers import publish_builtin_revision, seed_workspace_agent_definitions

    publish_builtin_revision(JobQueries(TEST_DATABASE_URL, Path(tempfile.mkdtemp())), workspace_id)
    seed_workspace_agent_definitions(workspace_id)
    service = AgentService(TEST_DATABASE_URL, workspace_id)
    service.save_draft(
        "example-write-script-v1",
        AgentDefinition(
            capability="write_script",
            runtime="velites",
            skill="education-video-problems-generation/write-script",
            config_schema={
                "type": "object",
                "properties": {
                    "api_url": {"type": "string"},
                    "token": {"type": "string", "secret": True},
                },
            },
        ),
        created_by="test-seed",
    )
    service.publish("example-write-script-v1")


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
        json={"id": "education_video_problems_generation", "name": "secrets-ws"},
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
    _publish_secret_node_schema(workspace_id)

    saved = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={
            "nodeConfig": {
                "write_script": {
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
    config = fetched.json()["settings"]["nodeConfig"]["write_script"]
    assert config["token"] == {"secret_set": True}
    assert config["api_url"] == "http://cms.example.com/question/detail"

    # Persistence holds only the secret_ref marker; plaintext lives in the vault.
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["education_video_problems_generation"]["write_script"]
    assert stored["token"] == {
        "secret_ref": "node:education_video_problems_generation:write_script:token"
    }
    assert PLAINTEXT not in str(workspace["node_config"])
    listed = admin_client.get(f"/api/workspaces/{workspace_id}/secrets")
    assert [entry["name"] for entry in listed.json()["secrets"]] == [
        "node:education_video_problems_generation:write_script:token"
    ]


def test_node_config_resave_without_secret_keeps_ref(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    _publish_secret_node_schema(workspace_id)
    patch = {"nodeConfig": {"write_script": {"token": PLAINTEXT}}}
    assert (
        admin_client.patch(f"/api/workspaces/{workspace_id}/settings/nodes", json=patch).status_code
        == 200
    )

    resaved = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={"nodeConfig": {"write_script": {"api_url": "http://cms.example.com/other"}}},
    )
    assert resaved.status_code == 200, resaved.text
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["education_video_problems_generation"]["write_script"]
    assert stored["token"] == {
        "secret_ref": "node:education_video_problems_generation:write_script:token"
    }


def test_node_config_masked_echo_keeps_stored_ref(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    _publish_secret_node_schema(workspace_id)
    assert (
        admin_client.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"write_script": {"token": PLAINTEXT}}},
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
    stored = workspace["node_config"]["education_video_problems_generation"]["write_script"]
    assert stored["token"] == {
        "secret_ref": "node:education_video_problems_generation:write_script:token"
    }


def test_node_config_empty_secret_clears_vault_entry(admin_client, app, vault_key):
    workspace_id = _create_workspace(admin_client)
    _publish_secret_node_schema(workspace_id)
    assert (
        admin_client.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={
                "nodeConfig": {
                    "write_script": {
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
                "write_script": {
                    "api_url": "http://cms.example.com/question/detail",
                    "token": "",
                }
            }
        },
    )
    assert cleared.status_code == 200, cleared.text
    workspace = app.state.job_db.get_workspace(workspace_id)
    stored = workspace["node_config"]["education_video_problems_generation"]["write_script"]
    assert "token" not in stored
    assert stored["api_url"] == "http://cms.example.com/question/detail"
    listed = admin_client.get(f"/api/workspaces/{workspace_id}/secrets")
    assert listed.json() == {"secrets": []}
    config = cleared.json()["settings"]["nodeConfig"]["write_script"]
    assert config["token"] == {"secret_set": False}


def test_node_config_save_without_master_key_fails(admin_client, monkeypatch):
    # See test_secret_put_requires_master_key: patch the resolver, not env.
    monkeypatch.setattr(
        "server.app.services.vault.resolve_master_key", lambda *_args, **_kwargs: None
    )
    workspace_id = _create_workspace(admin_client)
    _publish_secret_node_schema(workspace_id)

    response = admin_client.patch(
        f"/api/workspaces/{workspace_id}/settings/nodes",
        json={"nodeConfig": {"write_script": {"token": PLAINTEXT}}},
    )

    assert response.status_code == 400
    assert "AGENT_LEGION_VAULT_MASTER_KEY" in response.json()["detail"]
