"""Artifact references for remote claim inputs and result outputs (phase 4, task 3).

Covers: payload builders marking the manifest ``bundle_mode: "refs"`` and
skipping bundle inputs once every input landed in the ArtifactStore (with
silent fallback to the full bundle), the completion callback registering
``output_artifacts`` refs, ``report_result`` validating referenced hashes
against the store, and the worker pulling refs inputs / uploading outputs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.remote import worker
from server.app.db.schema import init_db
from server.app.executors.config import RemoteCapabilityConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ClaimedExecution, ExecutionContext
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)
from server.app.executors.remote_bundle import build_bundle
from server.app.executors.remote_completion import RemoteCompletionHandler
from server.app.executors.remote_payloads.openclaw import OpenClawPayloadBuilder
from server.app.executors.remote_payloads.pi import PiPayloadBuilder
from server.app.executors.runtime_config import OpenClawRuntimeConfig, PiRuntimeConfig
from server.app.jobs import JobQueries
from server.app.routes.remote import create_remote_router
from server.app.services.artifact_store import ArtifactStore
from server.app.workflows.pi_protocol import render_command_spec
from tests.executors.adapters.helpers import _make_skill_manager
from tests.executors.leases.helpers import _claim_request, _setup_workspace
from tests.postgres_support import TEST_DATABASE_URL

INPUT_BYTES = b'{"question": "1+1=?"}'

# ---- payload builder: bundle_mode on the manifest ----


def _execution_context(
    tmp_path: Path, job_id: str, inputs: tuple[str, ...] = ("in.json",)
) -> ExecutionContext:
    job_dir = tmp_path / "job"
    job_dir.mkdir(exist_ok=True)
    for rel in inputs:
        (job_dir / rel).write_bytes(INPUT_BYTES)
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="test",
        workspace_id="ws-a",
        job_id=job_id,
        workflow_key="video_knowledge",
        node_key="gen",
        capability="cap",
        workspace={"id": "ws-a"},
        job={"id": job_id},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=inputs,
        expected_outputs=("out.json",),
    )


def _store_and_job(tmp_path: Path) -> tuple[ArtifactStore, str]:
    # artifact_refs.job_id references jobs(id): staging needs a real job row.
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    job_db = JobQueries(db_path, tmp_path / "jobs")
    _, job_id = _setup_workspace(job_db, "WS", "test-exec", workspace_limit=5, local_limit=None)
    return ArtifactStore(tmp_path / "artifacts", db_path), job_id


def _pi_builder(tmp_path: Path, store: ArtifactStore | None = None) -> PiPayloadBuilder:
    return PiPayloadBuilder(
        PiRuntimeConfig(),
        _make_skill_manager(
            tmp_path, "video_knowledge/gen", validate_script="#!/usr/bin/env python3\n"
        ),
        {"cap": RemoteCapabilityConfig(skill="video_knowledge/gen")},
        artifact_store=store,
    )


def _bundle_names(bundle_path: Path) -> list[str]:
    with tarfile.open(bundle_path, "r:gz") as tar:
        return tar.getnames()


def test_pi_manifest_refs_mode_skips_bundle_inputs(tmp_path: Path) -> None:
    store, job_id = _store_and_job(tmp_path)
    builder = _pi_builder(tmp_path, store)
    context = _execution_context(tmp_path, job_id)
    manifest = builder.build_manifest(context)
    bundle = tmp_path / "bundle.tar.gz"
    builder.build_bundle_for(context, bundle)

    digest = hashlib.sha256(INPUT_BYTES).hexdigest()
    assert manifest["bundle_mode"] == "refs"
    assert manifest["input_artifacts"] == {"in.json": f"sha256:{digest}"}
    assert manifest["artifact_upload_url"] == "/api/artifacts"
    names = _bundle_names(bundle)
    assert "manifest.json" in names
    assert not any(name.startswith("inputs") for name in names)
    with tarfile.open(bundle, "r:gz") as tar:
        bundled = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    assert bundled["bundle_mode"] == "refs"
    refs = store.refs_for_job(job_id)
    assert {(r["node_key"], r["name"], r["hash"]) for r in refs} == {("gen", "in.json", digest)}


def test_pi_manifest_without_store_fails_loudly(tmp_path: Path) -> None:
    builder = _pi_builder(tmp_path)
    context = _execution_context(tmp_path, "job-1")
    builder.build_manifest(context)
    bundle = tmp_path / "bundle.tar.gz"
    with pytest.raises(RuntimeError, match="artifact store is required"):
        builder.build_bundle_for(context, bundle)


def test_pi_manifest_staging_failure_raises(tmp_path: Path, monkeypatch) -> None:
    store, job_id = _store_and_job(tmp_path)
    builder = _pi_builder(tmp_path, store)
    context = _execution_context(tmp_path, job_id)

    def _failing_put(data: bytes) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(store, "put", _failing_put)
    builder.build_manifest(context)
    bundle = tmp_path / "bundle.tar.gz"
    with pytest.raises(OSError, match="disk full"):
        builder.build_bundle_for(context, bundle)


def test_openclaw_manifest_refs_mode_skips_bundle_inputs(tmp_path: Path) -> None:
    store, job_id = _store_and_job(tmp_path)
    builder = OpenClawPayloadBuilder(
        OpenClawRuntimeConfig(command_template=("openclaw", "run")),
        "agent-1",
        {"cap": RemoteCapabilityConfig(skill="video_knowledge/gen")},
        artifact_store=store,
    )
    context = _execution_context(tmp_path, job_id)
    manifest = builder.build_manifest(context)
    bundle = tmp_path / "bundle.tar.gz"
    builder.build_bundle_for(context, bundle)

    assert manifest["bundle_mode"] == "refs"
    assert manifest["input_artifacts"]["in.json"].startswith("sha256:")
    assert not any(name.startswith("inputs") for name in _bundle_names(bundle))


# ---- completion callback: output_artifacts refs registration ----

CAPABILITY = "review_keywords"


@pytest.fixture
def completion_rig(tmp_path: Path):
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    job_db = JobQueries(db_path, tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path, job_db=job_db)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles", claim_timeout_seconds=60.0)
    store = ArtifactStore(tmp_path / "artifacts", db_path)
    handler = RemoteCompletionHandler(broker, leases, tmp_path / "jobs", artifact_store=store)
    broker.register_completion_callback(handler.handle_completion)
    workspace_id, job_id = _setup_workspace(
        job_db, "WS", "pi-remote", workspace_limit=5, local_limit=None
    )
    claim = leases.try_claim(
        _claim_request(workspace_id, job_id, executor_id="pi-remote", local_node_limit=None)
    )
    assert claim is not None
    return broker, store, job_db, job_id, claim


def _submit_and_dequeue(
    broker: RemoteExecutionBroker, claim: ClaimedExecution, job_id: str
) -> None:
    broker.submit(
        RemoteExecutionPayload(
            execution_id=claim.execution_id,
            lease_id=claim.lease_id,
            job_id=job_id,
            node_key=claim.node_key,
            capability=CAPABILITY,
            bundle_name=f"{claim.execution_id}.tar.gz",
            manifest={
                "job_id": job_id,
                "node_key": claim.node_key,
                "run_token": "tok",
                "expected_outputs": ["out.json"],
            },
        )
    )
    assert broker.dequeue("w1", {CAPABILITY}) is not None


def _write_result_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        data = b"{}"
        info = tarfile.TarInfo("out.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def test_completion_registers_output_artifacts(completion_rig, tmp_path: Path) -> None:
    broker, store, job_db, job_id, claim = completion_rig
    _submit_and_dequeue(broker, claim, job_id)
    digest = store.put(b"{}")  # worker uploads outputs before reporting
    archive = broker.bundle_dir / f"{claim.execution_id}.result.tar.gz"
    _write_result_archive(archive)
    outcome = RemoteOutcome(
        status="completed",
        exit_code=0,
        result_archive_name=archive.name,
        output_artifacts={"out.json": f"sha256:{digest}"},
    )

    assert broker.complete(claim.execution_id, "w1", outcome) is True
    broker.wait_idle()

    refs = store.refs_for_job(job_id)
    assert {(r["node_key"], r["name"], r["hash"]) for r in refs} == {
        (claim.node_key, "out.json", digest)
    }


def test_completion_without_output_artifacts_fails_refs_contract(completion_rig, tmp_path) -> None:
    broker, store, job_db, job_id, claim = completion_rig
    _submit_and_dequeue(broker, claim, job_id)
    archive = broker.bundle_dir / f"{claim.execution_id}.result.tar.gz"
    _write_result_archive(archive)
    outcome = RemoteOutcome(status="completed", exit_code=0, result_archive_name=archive.name)

    assert broker.complete(claim.execution_id, "w1", outcome) is True
    broker.wait_idle()

    node = job_db.get_job_node(job_id, claim.node_key)
    assert node is not None and node["status"] == "failed"
    assert node["error_message"] == "worker did not report output artifacts"
    assert store.refs_for_job(job_id) == []


# ---- report_result: output_artifacts validation ----

TOKEN = "test-token"
HEADERS = {"X-Worker-Token": TOKEN, "X-Worker-Id": "w1"}


@pytest.fixture
def route_rig(tmp_path: Path, settings):
    remote = settings.executor_runtime.remote.model_copy(
        update={"worker_token": TOKEN, "min_worker_protocol_version": 0}
    )
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    remote_settings = dataclasses.replace(settings, executor_runtime=runtime)
    init_db(TEST_DATABASE_URL)
    broker = RemoteExecutionBroker(TEST_DATABASE_URL, tmp_path / "bundles")
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    app = FastAPI()
    app.include_router(create_remote_router(broker, remote_settings, store), prefix="/api")
    HEADERS["X-Worker-Token"] = broker.issue_worker_token("w1", "w1", ["cap_a"], 1)
    broker.bundle_dir.mkdir(parents=True, exist_ok=True)
    (broker.bundle_dir / "e1.tar.gz").write_bytes(b"bundle-bytes")
    broker.submit(
        RemoteExecutionPayload(
            execution_id="e1",
            lease_id="lease-e1",
            job_id="job1",
            node_key="node_a",
            capability="cap_a",
            bundle_name="e1.tar.gz",
            manifest={"job_id": "job1", "node_key": "node_a", "run_token": "abc123"},
        )
    )
    client = TestClient(app)
    resp = client.post(
        "/api/remote/claim", json={"worker_id": "w1", "capabilities": ["cap_a"]}, headers=HEADERS
    )
    assert resp.status_code == 200
    return client, broker, store


def _report(client: TestClient, meta: dict):
    return client.post(
        "/api/remote/executions/e1/result",
        headers={**HEADERS, "X-Remote-Result": json.dumps(meta)},
        content=b"archive-bytes",
    )


def test_report_result_rejects_unknown_output_artifact(route_rig) -> None:
    client, _, _ = route_rig
    meta = {
        "status": "completed",
        "exit_code": 0,
        "output_artifacts": {"out.json": "sha256:" + "ab" * 32},
    }
    resp = _report(client, meta)
    assert resp.status_code == 409
    assert "upload" in resp.json()["detail"]


def test_report_result_rejects_malformed_output_artifact_hash(route_rig) -> None:
    client, _, _ = route_rig
    meta = {
        "status": "completed",
        "exit_code": 0,
        "output_artifacts": {"out.json": "not-a-hash"},
    }
    assert _report(client, meta).status_code == 400


def test_report_result_accepts_uploaded_output_artifacts(route_rig) -> None:
    client, broker, store = route_rig
    digest = store.put(b"output-bytes")
    meta = {
        "status": "completed",
        "exit_code": 0,
        "output_artifacts": {"out.json": f"sha256:{digest}"},
    }
    assert _report(client, meta).status_code == 204
    outcome = broker.wait_result("e1")
    assert outcome.output_artifacts == {"out.json": f"sha256:{digest}"}


# ---- worker: refs inputs pull + outputs upload ----

FAKE_PI_ECHO = """#!/usr/bin/env python3
import json
from pathlib import Path
data = Path("input.json").read_bytes()
Path("output.json").write_bytes(data)
print(json.dumps({"type": "done"}))
"""


def _refs_manifest(fake_pi: Path, input_digest: str) -> dict:
    return {
        "job_id": "job-1",
        "node_key": "node_a",
        "capability": "extract_keywords",
        "inputs": ["input.json"],
        "expected_outputs": ["output.json"],
        "additional_prompt": "",
        "tools": ["read", "write", "bash"],
        "skill": "wf/extract",
        "skill_version": "abc",
        "run_token": "tok123",
        "bundle_mode": "refs",
        "input_artifacts": {"input.json": f"sha256:{input_digest}"},
        "artifact_upload_url": "/api/artifacts",
        "pi": {
            "binary": str(fake_pi),
            "provider": "deepseek",
            "model": "your-model-b",
            "thinking": "low",
            "timeout_seconds": 600,
            "environment": {},
        },
    }


def _write_fake_pi(tmp_path: Path, script: str = FAKE_PI_ECHO) -> Path:
    fake = tmp_path / "fake_pi"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return fake


class RefStubClient:
    """In-memory WorkerClient double: bundle download plus artifact endpoints."""

    def __init__(self, bundle: Path, artifacts: dict[str, bytes]) -> None:
        self._bundle = bundle
        self._artifacts = artifacts
        self.downloaded: list[str] = []
        self.uploaded: dict[str, bytes] = {}

    def download_bundle(self, claim: dict, dest: Path) -> None:
        dest.write_bytes(self._bundle.read_bytes())

    def heartbeat(self, execution_id: str) -> bool:
        return True

    def download_artifact(self, hash: str, dest: Path) -> None:
        self.downloaded.append(hash)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._artifacts[hash])

    def upload_artifact(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self.uploaded[digest] = data
        return digest


class FullOnlyStubClient:
    """Legacy-mode double without artifact endpoints; any artifact call fails."""

    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    def download_bundle(self, claim: dict, dest: Path) -> None:
        dest.write_bytes(self._bundle.read_bytes())

    def heartbeat(self, execution_id: str) -> bool:
        return True


def test_worker_refs_mode_pulls_inputs_and_uploads_outputs(tmp_path: Path) -> None:
    input_digest = hashlib.sha256(INPUT_BYTES).hexdigest()
    manifest = _refs_manifest(_write_fake_pi(tmp_path), input_digest)
    empty_job_src = tmp_path / "job_src"
    empty_job_src.mkdir()
    bundle = tmp_path / "e1.tar.gz"
    build_bundle(bundle, manifest=manifest)
    claim = {
        "execution_id": "e1",
        "manifest": manifest,
        "command_spec": render_command_spec(manifest),
    }
    client = RefStubClient(bundle, {input_digest: INPUT_BYTES})

    metadata, archive = worker.run_execution(client, claim, tmp_path / "work")

    assert metadata["status"] == "completed"
    assert client.downloaded == [input_digest]
    job_dir = tmp_path / "work" / "e1" / "job"
    assert (job_dir / "input.json").read_bytes() == INPUT_BYTES
    # The fake agent echoes input.json into output.json, so the digests match.
    assert metadata["output_artifacts"] == {"output.json": f"sha256:{input_digest}"}
    assert client.uploaded[input_digest] == INPUT_BYTES
    assert archive is not None and archive.is_file()  # archive still shipped for compat


def test_worker_rejects_non_refs_bundle(tmp_path: Path) -> None:
    fake = _write_fake_pi(
        tmp_path,
        "#!/usr/bin/env python3\nimport json\n"
        "from pathlib import Path\n"
        "Path('output.json').write_text('{}', encoding='utf-8')\n"
        "print(json.dumps({'type': 'done'}))\n",
    )
    manifest = _refs_manifest(fake, "ab" * 32)
    del manifest["bundle_mode"], manifest["input_artifacts"], manifest["artifact_upload_url"]
    job_src = tmp_path / "job_src"
    job_src.mkdir()
    (job_src / "input.json").write_bytes(INPUT_BYTES)
    bundle = tmp_path / "e1.tar.gz"
    build_bundle(bundle, manifest=manifest)
    claim = {
        "execution_id": "e1",
        "manifest": manifest,
        "command_spec": render_command_spec(manifest),
    }

    with pytest.raises(RuntimeError, match="refs"):
        worker.run_execution(FullOnlyStubClient(bundle), claim, tmp_path / "work")
