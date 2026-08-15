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
    has_online_code_worker,
    resolve_code_manifest_config,
    split_manifest_config,
)
from server.app.agent_broker.code_eligibility import is_worker_eligible
from server.app.agent_workers import AgentWorkerRegistry
from server.app.executors.config import CodeCapabilityConfig
from server.app.services.artifact_store import ArtifactStore
from server.app.services.vault import VaultService
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL
from worker.code_runner import build_child_payload

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
    workspace = job_db.create_workspace(
        default_workflow_key="question_comprehension_info", name="test-workspace"
    )
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


def _settings(tmp_path: Path, config: dict | None = None) -> Settings:
    return Settings(
        root_dir=REPO_ROOT,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config=config or {},
        database_url=TEST_DATABASE_URL,
    )


def _service(job_db, tmp_path: Path, config: dict | None = None) -> CodeDispatchService:
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=tmp_path, bundle_dir=tmp_path / "bundles"
    )
    return CodeDispatchService(
        _settings(tmp_path, config),
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
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'question_comprehension_info')"
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


_SENSITIVE_CONFIG = {
    "vault": {"master_key": "fernet-key-material"},
    "auth": {"bootstrap_admin_password": "bootstrap-pw"},
    "database": {"url": "postgresql://user:db-pw@db/agent_legion"},
    "agent_workers": {"register_token": "management-secret"},
    "server": {"cors": {"allow_origins": ["https://example.com"]}},
    "asr": {"whisper": {"binary": "/env/whisper-cli"}},
}


def test_enqueue_strips_instance_settings_from_manifest_and_child_payload(job_db, tmp_path) -> None:
    """VAULT-SECRET-001: only node-consumed settings sections may ride the
    manifest — the vault master key, DB DSN and register token never persist
    nor cross into the Worker sandbox stdin payload."""
    _insert_job(job_db)
    service = _service(job_db, tmp_path, config=_SENSITIVE_CONFIG)

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
        is True
    )

    with job_db._connect_read() as conn:
        row = conn.execute("select manifest_json from agent_execution_requests").fetchone()
    manifest = json.loads(row["manifest_json"])
    settings_config = manifest["runtime_context"]["settings_config"]
    assert settings_config == {"asr": {"whisper": {"binary": "/env/whisper-cli"}}}
    # The manifest log_path is data-dir-relative, not a Host path leak.
    assert manifest["log_path"] == "logs/jobs/job-1-package.log"
    for leaked in ("fernet-key-material", "bootstrap-pw", "db-pw", "management-secret"):
        assert leaked not in row["manifest_json"]
    # The sandbox child payload inherits the same whitelist end to end.
    payload = build_child_payload(manifest, _CODE, tmp_path / "child")
    assert payload["runtime"]["settings_config"] == settings_config


def _register_probe_worker(
    worker_id: str,
    *,
    capabilities: list[str] | None = None,
    max_code_concurrency: int = 1,
    allowed_workspaces: list[str] | None = None,
) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name="worker",
        runtimes=["pi"],
        capabilities=capabilities,
        max_concurrency=10,
        max_code_concurrency=max_code_concurrency,
        protocol_version=2,
        allowed_workspaces=allowed_workspaces,
    )


def test_online_code_worker_probe_matches_capability(job_db) -> None:
    assert has_online_code_worker(TEST_DATABASE_URL, "package", "test-workspace") is False
    _register_probe_worker("worker-a", capabilities=["package"])

    assert has_online_code_worker(TEST_DATABASE_URL, "package", "test-workspace") is True
    # Code capacity but no matching declaration: the request would rot in
    # queued (no timeout fallback), so the probe says no and the scheduler
    # falls back to local execution.
    assert has_online_code_worker(TEST_DATABASE_URL, "transcribe", "test-workspace") is False


def test_online_code_worker_probe_wildcard_zero_capacity_and_revoked(job_db) -> None:
    _register_probe_worker("worker-wild", capabilities=None)  # legacy "*" mode
    assert has_online_code_worker(TEST_DATABASE_URL, "anything", "test-workspace") is True
    AgentWorkerRegistry(TEST_DATABASE_URL).revoke("worker-wild")
    assert has_online_code_worker(TEST_DATABASE_URL, "anything", "test-workspace") is False
    # An agent-only Worker (no code pool) never counts even on a match.
    _register_probe_worker("worker-agent", capabilities=["anything"], max_code_concurrency=0)
    assert has_online_code_worker(TEST_DATABASE_URL, "anything", "test-workspace") is False


def test_online_code_worker_probe_matches_claim_side_filters(job_db) -> None:
    """The probe must mirror claim_evaluate.py: a Worker outside the
    workspace's admission scope (or below protocol v2) can never claim the
    request, and with no queued-timeout fallback the request would wedge the
    job — so the probe says no and the node falls back to local execution."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('other-workspace', 'Other', 'question_comprehension_info')"
        )
    _register_probe_worker(
        "worker-scoped",
        capabilities=["package"],
        allowed_workspaces=["other-workspace"],
    )
    assert has_online_code_worker(TEST_DATABASE_URL, "package", "test-workspace") is False
    assert has_online_code_worker(TEST_DATABASE_URL, "package", "other-workspace") is True
    # An unrestricted Worker (empty allowed_workspaces) admits any workspace.
    _register_probe_worker("worker-open", capabilities=["package"])
    assert has_online_code_worker(TEST_DATABASE_URL, "package", "test-workspace") is True

    # A v1 row predating the register-time gate never counts either.
    AgentWorkerRegistry(TEST_DATABASE_URL).revoke("worker-scoped")
    AgentWorkerRegistry(TEST_DATABASE_URL).revoke("worker-open")
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_workers(worker_id, runtimes_json, max_concurrency,"
            " max_code_concurrency, capabilities_json, protocol_version, token_hash,"
            " registered_at, last_seen_at)"
            " values ('worker-v1', '[\"pi\"]', 10, 1, '[\"package\"]', 1, 'x',"
            " current_timestamp, current_timestamp)"
        )
    assert has_online_code_worker(TEST_DATABASE_URL, "package", "test-workspace") is False


def test_online_probe_caches_per_capability_within_ttl(job_db, tmp_path) -> None:
    service = _service(job_db, tmp_path)
    _register_probe_worker("worker-a", capabilities=["package"])

    assert service.online_code_worker_available("package", "test-workspace") is True
    assert service.online_code_worker_available("transcribe", "test-workspace") is False
    # Within the TTL the cached answer is served even after the Worker leaves.
    AgentWorkerRegistry(TEST_DATABASE_URL).revoke("worker-a")
    assert service.online_code_worker_available("package", "test-workspace") is True
    service._online_probe.clear()
    assert service.online_code_worker_available("package", "test-workspace") is False
