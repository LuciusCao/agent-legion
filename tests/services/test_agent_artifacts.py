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
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'demo_workflow') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, "
            " title, status, storage_dir) values (%s, 'ws', 's', 's1', 't', 'pending', 'd')",
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


def test_stage_agent_inputs_dedupes_normalized_aliases(tmp_path: Path) -> None:
    """INV-9（#876 P2-a）冻结点去重：重复声明（含 ./ 拼写）只读一次源文
    件、只 put 一次 CAS、refs 只记归一化单键——序语义在冻结点消失，
    Worker/Host/任何序无从分叉。声明列表（context.inputs）不在本函数
    职责内，不由它改写。"""
    store = _make_store(tmp_path)
    _make_job("job-1")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    payload = b"same-file"
    (job_dir / "in.json").write_bytes(payload)
    manifest: dict = {}
    put_count = 0
    original_put = store.put

    def _counting_put(data: bytes) -> str:
        nonlocal put_count
        put_count += 1
        return original_put(data)

    store.put = _counting_put  # type: ignore[method-assign]
    stage_agent_inputs(store, _context(job_dir, ("in.json", "./in.json", "in.json")), manifest)

    digest = hashlib.sha256(payload).hexdigest()
    assert put_count == 1  # 单次 put
    assert manifest["input_artifacts"] == {"in.json": f"sha256:{digest}"}  # 归一化单键
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select name, hash from artifact_refs where job_id='job-1' and node_key='node-a'"
        ).fetchall()
    assert [(row["name"], row["hash"]) for row in rows] == [("in.json", digest)]


def test_stage_agent_inputs_skips_unsafe_names(tmp_path: Path) -> None:
    """与视图侧同一 safe_relative 语义：不安全名（绝对路径/..）不冻结、
    不进 refs——顺带关掉 dispatch 侧的越界读（此前 job_dir/../x 会被读
    出）。"""
    store = _make_store(tmp_path)
    _make_job("job-1")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (tmp_path / "escape.json").write_bytes(b"outside")
    (job_dir / "in.json").write_bytes(b"inside")
    manifest: dict = {}

    stage_agent_inputs(
        store, _context(job_dir, ("../escape.json", "/abs/x.json", "in.json")), manifest
    )

    digest = hashlib.sha256(b"inside").hexdigest()
    assert manifest["input_artifacts"] == {"in.json": f"sha256:{digest}"}
