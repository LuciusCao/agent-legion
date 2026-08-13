"""Code dispatch: manifest secret hygiene, bundle shape, eligibility scan.

Batch 2 (design §7.2/§7.3): the queued kind='code' manifest must never hold
plaintext secrets (VAULT-SECRET-001) — secret-marked keys persist only as
vault references and are re-resolved on the claim-response path.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.agent_bundle import CODE_BUNDLE_LIBS_DIR, CODE_BUNDLE_NODE_FILE
from server.app.agent_broker.code_dispatch import (
    CodeDispatchService,
    PlaintextSecretError,
    resolve_code_manifest_config,
    split_manifest_config,
)
from server.app.agent_broker.code_eligibility import is_worker_eligible
from server.app.executors.config import CodeCapabilityConfig
from server.app.services.artifact_store import ArtifactStore
from server.app.services.vault import VaultService
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL

REPO_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = {
    "properties": {
        "mode": {"type": "string"},
        "token": {"type": "string", "secret": True},
    }
}


@pytest.mark.no_db
def test_split_manifest_config_separates_secret_refs() -> None:
    config, secret_config = split_manifest_config(
        _SCHEMA,
        {"mode": "fast", "token": {"secret_ref": "api-token"}, "unknown": "dropped"},
    )

    assert config == {"mode": "fast"}
    assert secret_config == {"token": {"secret_ref": "api-token"}}


@pytest.mark.no_db
def test_split_manifest_config_rejects_plaintext_secret() -> None:
    with pytest.raises(PlaintextSecretError, match="legacy plaintext"):
        split_manifest_config(_SCHEMA, {"token": "plaintext-token"})


@pytest.mark.no_db
def test_split_manifest_config_drops_empty_secret() -> None:
    assert split_manifest_config(_SCHEMA, {"token": ""}) == ({}, {})


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import json\nfrom workspace_libs.node_sdk import NodeContext\n", True),
        ("import requests\n", True),
        ("from workspace_libs.cms import urls\n", True),
        ("import server.app.pipeline.download\n", False),
        ("from server.app import settings\n", False),
        ("import workflow_nodes.video_download\n", False),
        ("import importlib\n", False),
        ("x = __import__('os')\n", False),
        ("import os\nimport boto3\n", False),
        ("def run(job, job_dir, runtime):\n    pass\n", True),
    ],
)
def test_worker_eligibility_scan(code: str, expected: bool) -> None:
    assert is_worker_eligible(code, REPO_ROOT) is expected


def test_resolve_code_manifest_config_injects_and_strips_secrets(job_db, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    workspace = job_db.create_workspace("test-workspace")
    vault = VaultService(job_db.path, {})
    vault.set(workspace["id"], "api-token", "s3cr3t")
    manifest = {
        "kind": "code",
        "workspace_id": workspace["id"],
        "config_schema": _SCHEMA,
        "config": {"mode": "fast"},
        "secret_config": {"token": {"secret_ref": "api-token"}},
    }

    resolved = resolve_code_manifest_config(manifest, job_db.path, {})

    assert resolved["config"] == {"mode": "fast", "token": "s3cr3t"}
    assert "secret_config" not in resolved
    # The stored manifest is untouched (still secret-free).
    assert manifest["secret_config"] == {"token": {"secret_ref": "api-token"}}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=REPO_ROOT,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )


def _service(job_db, tmp_path: Path) -> CodeDispatchService:
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=tmp_path, bundle_dir=tmp_path / "bundles"
    )
    return CodeDispatchService(
        _settings(tmp_path),
        broker,
        ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL),
        job_db,
    )


def _node() -> WorkflowNode:
    return WorkflowNode(key="package", label="package", capability="package", outputs=["out.json"])


_CODE = "def run(job, job_dir, runtime):\n    pass\n"


def _insert_job(job_db, job_id: str = "job-1") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('test-workspace', 'Test')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'package')", (job_id,))


def test_enqueue_builds_secret_free_manifest_and_bundle(job_db, tmp_path) -> None:
    _insert_job(job_db)
    service = _service(job_db, tmp_path)

    queued = service.enqueue(
        capability="package",
        capability_config=CodeCapabilityConfig(
            path="workflow_nodes/video_package.py",
            timeout_seconds=42,
            sandbox_network=True,
            config_schema=_SCHEMA,
        ),
        workspace={"id": "test-workspace"},
        job={"id": "job-1"},
        workflow_key="questions",
        node=_node(),
        job_dir=tmp_path / "job",
        log_path=tmp_path / "logs" / "jobs" / "job-1-package.log",
        inputs=(),
        code_text=_CODE,
        custom_code=False,
        config={"mode": "fast"},
        secret_config={"token": {"secret_ref": "api-token"}},
    )

    assert queued is True
    with job_db._connect_read() as conn:
        row = conn.execute("select * from agent_execution_requests").fetchone()
    assert row["kind"] == "code"
    assert row["agent_id"] == "package"
    manifest = json.loads(row["manifest_json"])
    assert manifest["code_hash"] == hashlib.sha256(_CODE.encode()).hexdigest()
    assert manifest["config"] == {"mode": "fast"}
    assert manifest["secret_config"] == {"token": {"secret_ref": "api-token"}}
    assert manifest["expected_outputs"] == ["out.json"]
    assert manifest["timeout_seconds"] == 42
    assert manifest["sandbox_network"] is True
    assert manifest["bundle_mode"] == "refs"
    assert manifest["custom_code"] is False
    # The persisted document must not contain any plaintext secret value.
    assert "s3cr3t" not in row["manifest_json"]
    bundle = tmp_path / "bundles" / str(manifest["bundle_name"])
    with tarfile.open(bundle, "r:gz") as tar:
        names = tar.getnames()
        code_member = tar.extractfile(CODE_BUNDLE_NODE_FILE)
        assert code_member is not None
        code_text = code_member.read().decode()
    assert code_text == _CODE
    assert f"{CODE_BUNDLE_LIBS_DIR}/node_sdk.py" in names
    assert f"{CODE_BUNDLE_LIBS_DIR}/code_child.py" in names
    assert "manifest.json" not in names

    # Idempotency: a second enqueue for the same (job, node) is a no-op.
    assert (
        service.enqueue(
            capability="package",
            capability_config=CodeCapabilityConfig(path="workflow_nodes/video_package.py"),
            workspace={"id": "test-workspace"},
            job={"id": "job-1"},
            workflow_key="questions",
            node=_node(),
            job_dir=tmp_path / "job",
            log_path=tmp_path / "logs" / "jobs" / "job-1-package.log",
            inputs=(),
            code_text=_CODE,
            custom_code=False,
            config={},
            secret_config={},
        )
        is False
    )
