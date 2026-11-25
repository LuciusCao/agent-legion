"""Real Host dispatch/claim and Worker execution for shared resources (#1553 M44)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.code_dispatch import CodeDispatchService
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.executors.contracts import CodeCapabilityConfig
from server.app.routes.agent_worker_claim_response import build_claim_response
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from shared import code_sandbox
from shared.material_cache import MaterializeError
from shared.protocol import SHARED_RESOURCE_PROTOCOL_VERSION
from tests.fakes.storage import etag_of
from tests.services.shared_resources.conftest import TARGET_WS
from tests.services.shared_resources.worker_execution_helpers import create_worker_resource_job
from worker.artifact.inputs import download_input_artifacts
from worker.code_runner import execute_code
from worker.status import ExecutionStatusReporter

pytestmark = pytest.mark.full_gate
pytest_plugins = ("tests.services.shared_resources.conftest",)
REPO_ROOT = Path(__file__).resolve().parents[2]


class _WorkerClient:
    def __init__(self, bundle: Path | None = None, artifacts: dict[str, bytes] | None = None):
        self.bundle = bundle
        self.artifacts = artifacts or {}

    def download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("/api/artifacts/"):
            destination.write_bytes(self.artifacts[url.rsplit("/", 1)[-1]])
        else:
            assert self.bundle is not None
            destination.write_bytes(self.bundle.read_bytes())


def _fake_velites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "velites"
    script.write_text(
        '#!/usr/bin/env bash\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(
        shutil, "which", lambda binary: str(script) if binary == "velites" else None
    )


def _install_object_downloads(monkeypatch: pytest.MonkeyPatch, storage) -> None:
    prefix = "https://s3.test/download/"

    def open_download(url: str, *, if_match: str = ""):
        payload = storage.objects[url.removeprefix(prefix)]
        if if_match and if_match != etag_of(payload):
            raise MaterializeError("ETag mismatch")
        return io.BytesIO(payload)

    monkeypatch.setattr("worker.material_fetch._open_download", open_download)
    monkeypatch.setattr("worker.resource_fetch._open_download", open_download)


def test_host_worker_agent_shared_resource_round_trip(
    job_db,
    settings,
    shared_service,
    shared_materials,
    shared_storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code_text = (REPO_ROOT / "workflow_nodes" / "shared_resource_prepare.py").read_text(
        encoding="utf-8"
    )
    resource_id, job, _manifest = create_worker_resource_job(
        job_db,
        settings,
        shared_service,
        shared_materials,
        shared_storage,
        code_text=code_text,
    )
    definition = definition_from_job_snapshot(job)
    assert definition is not None
    node = definition.nodes["prepare"]
    settings.root_dir = REPO_ROOT
    broker = AgentExecutionBroker(
        job_db.dsn_identity, data_dir=tmp_path, bundle_dir=tmp_path / "bundles"
    )
    artifacts = ArtifactStore(tmp_path / "artifacts", job_db.dsn_identity)
    dispatch = CodeDispatchService(settings, broker, artifacts, job_db)
    assert dispatch.enqueue(
        capability=node.capability,
        capability_config=CodeCapabilityConfig(timeout_seconds=30, sandbox_network=False),
        workspace={"id": TARGET_WS},
        job=job,
        workflow_key=TARGET_WS,
        node=node,
        job_dir=tmp_path / "host-job",
        log_path=tmp_path / "logs" / "prepare.log",
        inputs=(),
        code_text=code_text,
        custom_code=True,
        config={},
        secret_config={},
    )
    with job_db._connect_read() as conn:
        queued = conn.execute(
            "select state, manifest_json from agent_execution_requests where job_id=%s",
            (job["id"],),
        ).fetchone()
    persisted = json.loads(queued["manifest_json"])
    assert persisted["shared_resources"] == [resource_id]
    assert "download_url" not in queued["manifest_json"]
    assert "shared_resources" not in persisted["runtime_context"]

    registry = AgentWorkerRegistry(job_db.dsn_identity)
    registry.issue_token(
        worker_id="worker-old",
        name="worker-old",
        runtimes=[],
        max_concurrency=1,
        max_code_concurrency=1,
        protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION - 1,
    )
    assert broker.claim("worker-old") is None
    with job_db._connect_read() as conn:
        state = conn.execute(
            "select state from agent_execution_requests where job_id=%s", (job["id"],)
        ).fetchone()
    assert state["state"] == "queued"

    registry.issue_token(
        worker_id="worker-current",
        name="worker-current",
        runtimes=[],
        max_concurrency=1,
        max_code_concurrency=1,
        protocol_version=SHARED_RESOURCE_PROTOCOL_VERSION,
    )
    claimed = broker.claim("worker-current")
    assert claimed is not None
    response = build_claim_response(
        broker,
        settings,
        JobArtifactObjectStore(job_db, shared_storage),
        {"protocol_version": SHARED_RESOURCE_PROTOCOL_VERSION},
        claimed,
    )
    claim = response.model_dump()
    descriptor = claim["manifest"]["runtime_context"]["shared_resources"]
    assert descriptor["pins"][0]["version"] == 1
    assert descriptor["pins"][0]["files"][0]["download_url"].startswith("https://s3.test/")

    _install_object_downloads(monkeypatch, shared_storage)
    _fake_velites(tmp_path, monkeypatch)
    bundle = tmp_path / "bundles" / claim["manifest"]["bundle_name"]
    task = execute_code(
        _WorkerClient(bundle),
        claim,
        tmp_path / "worker-root" / claim["execution_id"],
        {"node_key": "prepare"},
        threading.Semaphore(2),
        threading.Event(),
        1,
        threading.Event(),
        SimpleNamespace(proc_ref={}),
        ExecutionStatusReporter(None),
    )
    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed", task.code_result["error_message"]
    worker_output = (
        tmp_path / "worker-root" / claim["execution_id"] / "job" / "shared_resources.json"
    ).read_bytes()
    document = json.loads(worker_output)
    file = document["resources"][0]["files"][0]
    assert file["content"].encode() == b"resource-v1"
    assert file["sha256"] == hashlib.sha256(b"resource-v1").hexdigest()
    assert "download_url" not in document and "local_path" not in file

    output_digest = artifacts.put(worker_output)
    agent_dir = tmp_path / "agent-job"
    download_input_artifacts(
        _WorkerClient(artifacts={output_digest: worker_output}),
        {"input_artifacts": {"shared_resources.json": f"sha256:{output_digest}"}},
        agent_dir,
        threading.Semaphore(1),
    )
    agent_bytes = (agent_dir / "shared_resources.json").read_bytes()
    assert agent_bytes == worker_output
    agent_document = json.loads(agent_bytes)
    assert agent_document["resources"][0]["resource_id"] == resource_id
    assert agent_document["resources"][0]["files"][0]["content"].encode() == b"resource-v1"
