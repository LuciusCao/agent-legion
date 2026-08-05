from __future__ import annotations

import hashlib
from pathlib import Path

from server.app.agent_broker.agent_artifacts import stage_agent_inputs
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors.models import ExecutionContext
from server.app.services.artifact_store import ArtifactStore
from tests.postgres_support import TEST_DATABASE_URL


def _make_store(tmp_path: Path) -> ArtifactStore:
    init_db(TEST_DATABASE_URL)
    return ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)


def _make_job(job_id: str) -> None:
    """artifact_refs.job_id has a real FK to jobs(id); create a minimal job row."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('ws', 'ws') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values (?, 'ws', 'wf', 's', 's1', 't', 'pending', 'd')",
            (job_id,),
        )


def _context(job_dir: Path, inputs: tuple[str, ...]) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=1,
        executor_id="pi-1",
        workspace_id="ws",
        job_id="job-1",
        workflow_key="wf",
        node_key="node-a",
        capability="cap",
        workspace={},
        job={},
        job_dir=job_dir,
        log_path=job_dir / "run.log",
        inputs=inputs,
        expected_outputs=(),
    )


def test_stage_agent_inputs_uploads_inputs_and_rewrites_manifest(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _make_job("job-1")
    job_dir = tmp_path / "job"
    (job_dir / "inputs").mkdir(parents=True)
    payload = b'{"question": "1+1=?"}'
    (job_dir / "inputs" / "question.json").write_bytes(payload)
    manifest: dict = {}

    stage_agent_inputs(store, _context(job_dir, ("inputs/question.json",)), manifest)

    digest = hashlib.sha256(payload).hexdigest()
    assert manifest["bundle_mode"] == "refs"
    assert manifest["artifact_upload_url"] == "/api/artifacts"
    assert manifest["input_artifacts"] == {"inputs/question.json": f"sha256:{digest}"}
    assert (store.root / digest[:2] / digest).is_file()
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select name, hash from artifact_refs where job_id='job-1' and node_key='node-a'"
        ).fetchall()
    assert [(row["name"], row["hash"]) for row in rows] == [("inputs/question.json", digest)]


def test_stage_agent_inputs_handles_empty_inputs(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _make_job("job-1")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    manifest: dict = {}

    stage_agent_inputs(store, _context(job_dir, ()), manifest)

    assert manifest["bundle_mode"] == "refs"
    assert manifest["input_artifacts"] == {}
