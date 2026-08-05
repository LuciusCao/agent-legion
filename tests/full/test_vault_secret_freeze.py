"""Full-gate evidence for VAULT-SECRET-001 over a real database.

End-to-end chain: a resource binding secret is diverted to the vault, the
stored config and the intake freeze carry only the ``secret_ref`` marker, and
the runtime resolve chain turns the marker back into the plaintext in memory.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from server.app.services.job_intake import JobIntakeService
from server.app.services.vault import VaultService
from server.app.services.vault_resources import resource_secret_name
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.cms_helpers import _effective_cms_config

PLAINTEXT = "full-gate-secret-token"


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.mark.full_gate
def test_secret_ref_freeze_and_runtime_resolution(job_db, settings, vault_key) -> None:
    workspace = job_db.create_workspace(
        "vault-full", default_workflow_key="question_comprehension_info"
    )
    workspace_id = str(workspace["id"])
    definition = WorkflowCatalogService(settings).definition("question_comprehension_info")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace_id, definition)

    name = resource_secret_name("question_detail", "token")
    vault = VaultService(job_db.path, settings.config)
    vault.set(workspace_id, name, PLAINTEXT)
    job_db.update_workspace(
        workspace_id,
        resource_config={
            "resources": {
                "question_detail": {
                    "enabled": True,
                    "config": {
                        "api_url": "http://cms.example.com/question/detail",
                        "token": {"secret_ref": name},
                    },
                }
            }
        },
    )

    # Persistence: only ciphertext at rest, no plaintext anywhere in the DB row.
    with job_db.connect() as conn:
        row = conn.execute(
            "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
            (workspace_id, name),
        ).fetchone()
    assert row["ciphertext"] != PLAINTEXT
    assert PLAINTEXT not in row["ciphertext"]

    # Intake freeze: the batch payload carries the ref, never the plaintext.
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))
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
    frozen = json.loads(payload_text)["resource_config"]["resources"]["question_detail"]["config"]
    assert frozen["token"] == {"secret_ref": name}

    # Runtime resolve: the handler-facing chain sees the plaintext in memory.
    job = {"workspace_id": workspace_id, "batch_id": str(result["batch"]["id"])}
    context = {"settings_config": settings.config, "job_db": job_db}
    resolved = _effective_cms_config(job, context)
    assert resolved["token"] == PLAINTEXT
