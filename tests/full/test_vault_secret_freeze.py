"""Full-gate evidence for VAULT-SECRET-001 over a real database.

End-to-end chain: a node config secret is diverted to the vault, the stored
config and the intake freeze carry only the ``secret_ref`` marker, and the
dispatch-time resolve chain turns the marker back into the plaintext in
memory.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.services.agent_service import published_agent_definitions
from server.app.services.executor_definition_service import hydrate_executor_definitions
from server.app.services.job_intake import JobIntakeService
from server.app.services.node_secrets import node_secret_name
from server.app.services.vault import VaultService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_node_config import update_workspace_node_config

PLAINTEXT = "full-gate-secret-token"


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
    hydrate_executor_definitions(settings)
    workspace = job_db.create_workspace(
        "vault-full", default_workflow_key="question_comprehension_info"
    )
    workspace_id = str(workspace["id"])
    catalog = WorkflowCatalogService(settings)
    definition = catalog.definition("question_comprehension_info")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)

    name = node_secret_name("question_comprehension_info", "fetch_questions", "token")
    vault = VaultService(job_db.path, settings.config)
    update_workspace_node_config(
        job_db,
        catalog,
        published_agent_definitions(settings.database_url),
        job_db.get_workspace(workspace_id),
        {
            "nodeConfig": {
                "fetch_questions": {
                    "api_url": "http://cms.example.com/question/detail",
                    "token": PLAINTEXT,
                }
            }
        },
        settings.executor_definitions,
    )

    # Persistence: only ciphertext at rest, no plaintext anywhere in the DB rows.
    with job_db.connect() as conn:
        row = conn.execute(
            "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
            (workspace_id, name),
        ).fetchone()
    assert row["ciphertext"] != PLAINTEXT
    assert PLAINTEXT not in row["ciphertext"]
    stored = job_db.get_workspace(workspace_id)["node_config"]
    stored_node = stored["question_comprehension_info"]["fetch_questions"]
    assert stored_node["token"] == {"secret_ref": name}
    assert PLAINTEXT not in json.dumps(stored)

    # Intake freeze: the batch payload carries the ref, never the plaintext.
    service = JobIntakeService(job_db, settings, catalog)
    result = service.create_batch(
        workspace_id,
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "entity": "question",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
        },
    )
    batch = job_db.get_batch(str(result["batch"]["id"]))
    payload_text = str(batch["source_payload_json"])
    assert PLAINTEXT not in payload_text
    frozen = json.loads(payload_text)["node_config"]["fetch_questions"]
    assert frozen["token"] == {"secret_ref": name}

    # Runtime resolve: the dispatch chain sees the plaintext in memory.
    resolved = vault.resolve_secret_refs(frozen, workspace_id)
    assert resolved["token"] == PLAINTEXT
    assert resolved["api_url"] == "http://cms.example.com/question/detail"
