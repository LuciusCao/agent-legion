"""Full-gate evidence for VAULT-SECRET-001 over a real database.

End-to-end chain over the instance-level external connection mechanism (the
node-level CMS token secret was retired with it): a connection secret is
diverted to the instance vault, the stored config and the intake freeze carry
only the ``secret_ref`` marker / connection key, and the dispatch-time
resolve chain turns the marker back into the plaintext in memory.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.services.agent_service import published_agent_definitions
from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.connections import ConnectionService, connection_secret_name
from server.app.services.demo_node_seed import seed_demo_node_codes
from server.app.services.job_intake import JobIntakeService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_node_config import update_workspace_node_config

PLAINTEXT = "full-gate-secret-token"
CONNECTION_KEY = "cms-full"


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.mark.full_gate
def test_secret_ref_freeze_and_runtime_resolution(job_db, settings, vault_key) -> None:
    # The bare settings fixture does not hydrate executor definitions
    # (create_app does); the node config schema chain needs the seeded catalog.
    seed_demo_node_codes(settings)
    workspace = job_db.create_workspace(
        "vault-full", default_workflow_key="education_video_problems_generation"
    )
    workspace_id = str(workspace["id"])
    catalog = WorkflowCatalogService(settings)
    definition = catalog.definition("education_video_problems_generation")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)

    # Create the connection; the token is diverted to the instance vault.
    connections = ConnectionService(job_db.path, settings.config)
    connections.create(
        CONNECTION_KEY,
        "static_bearer",
        "CMS",
        {"base_url": "http://cms.example.com", "token": PLAINTEXT},
    )
    ref_name = connection_secret_name(CONNECTION_KEY, "token")

    # Persistence: only ciphertext at rest, no plaintext anywhere in the DB rows.
    with job_db.connect() as conn:
        row = conn.execute(
            "select ciphertext from instance_secrets where name=%s", (ref_name,)
        ).fetchone()
    assert row["ciphertext"] != PLAINTEXT
    assert PLAINTEXT not in row["ciphertext"]
    raw = connections._decode_config(connections._row(CONNECTION_KEY))
    assert raw["token"] == {"secret_ref": ref_name}
    assert raw["base_url"] == "http://cms.example.com"
    assert PLAINTEXT not in json.dumps(raw)

    # The demo nodes declare no connection property; republish the write_script
    # agent with a ``connection`` field so the schema chain accepts the
    # workspace override (agent config_schema is the D15 declaration point).
    from server.app.agent_catalog import AgentDefinition
    from server.app.services.agent_service import AgentService

    agent_service = AgentService(settings.database_url, workspace_id)
    agent_service.save_draft(
        "example-write-script-v1",
        AgentDefinition(
            capability="write_script",
            runtime="velites",
            skill="education-video-problems-generation/write-script",
            config_schema={
                "type": "object",
                "properties": {"connection": {"type": "string"}},
            },
        ),
        created_by="test-seed",
    )
    agent_service.publish("example-write-script-v1")

    # The node config references the connection by key only — no secret
    # material ever enters the workspace override.
    update_workspace_node_config(
        job_db,
        catalog,
        published_agent_definitions(settings.database_url, workspace_id),
        job_db.get_workspace(workspace_id),
        {"nodeConfig": {"write_script": {"connection": CONNECTION_KEY}}},
    )
    stored = job_db.get_workspace(workspace_id)["node_config"]
    stored_node = stored["education_video_problems_generation"]["write_script"]
    assert stored_node["connection"] == CONNECTION_KEY
    assert PLAINTEXT not in json.dumps(stored)

    # Intake freeze: the batch payload carries the connection key, never the
    # plaintext or a ref marker.
    service = JobIntakeService(job_db, settings, catalog)
    result = service.create_batch(
        workspace_id,
        {
            "workflow_key": "education_video_problems_generation",
            "source_kind": "direct_ids",
            "entity": "question",
            "knowledge_point_ids": ["Q1"],
        },
    )
    batch = job_db.get_batch(str(result["batch"]["id"]))
    payload_text = str(batch["source_payload_json"])
    assert PLAINTEXT not in payload_text
    assert ref_name not in payload_text
    frozen = json.loads(payload_text)["node_config"]["write_script"]
    assert frozen["connection"] == CONNECTION_KEY

    # Runtime resolve: the dispatch chain sees the plaintext in memory.
    resolved = ConnectionTokenService(job_db.path, settings.config).runtime_config(CONNECTION_KEY)
    assert resolved["token"] == PLAINTEXT
    assert resolved["base_url"] == "http://cms.example.com"
