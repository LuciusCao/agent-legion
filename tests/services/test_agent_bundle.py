from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from server.app.agent_broker.agent_bundle import (
    AgentBundleError,
    build_agent_bundle,
    cleanup_bundle_on_error,
    extract_agent_result,
)
from server.app.agent_broker.result_unpack import unpack_agent_result


def test_agent_bundle_contains_manifest_and_skill_snapshot(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Test", encoding="utf-8")
    archive = tmp_path / "bundle.tar.gz"

    build_agent_bundle(archive, skill_dir=skill, manifest={"agent_id": "generator-v1"})

    with tarfile.open(archive, "r:gz") as tar:
        assert {member.name for member in tar.getmembers()} >= {
            "manifest.json",
            "skill/SKILL.md",
        }


def test_agent_result_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "result.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"escape"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(AgentBundleError, match="unsafe path"):
        extract_agent_result(archive, tmp_path / "job")


def _write_result_archive(archive: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


def test_result_unpack_promotes_only_expected_outputs(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    archive = tmp_path / "result.tar.gz"
    _write_result_archive(
        archive,
        {
            "expected.json": b"{}",
            "evil.txt": b"clobber",
            "runs/generate/worker/events.jsonl": b"{}",
        },
    )

    unpack_agent_result(archive, job_dir, ("expected.json",))

    # Only the declared output is promoted; worker run logs (events.jsonl) and
    # undeclared files never land in the job dir, and staging leaves nothing.
    assert (job_dir / "expected.json").is_file()
    assert [path.name for path in job_dir.iterdir()] == ["expected.json"]


def test_concurrent_shard_results_share_job_dir_without_clobber(tmp_path: Path) -> None:
    """#401 review P1-2: sibling shards' result archives declare only their
    per-index output (the dispatch side excludes the node's ordinary outputs
    from shard manifests), so two shard results unpacking into the SAME job
    dir promote disjoint names — no last-writer-wins clobber even when both
    shards' code happened to write the same ordinary file."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    # The Worker packs only manifest expected_outputs — with the exclusion,
    # the ordinary file (out.json) never ships; per-index files are disjoint.
    shard0 = tmp_path / "shard0.tar.gz"
    _write_result_archive(shard0, {"shard_output-0.json": b'{"i": 0}'})
    shard1 = tmp_path / "shard1.tar.gz"
    _write_result_archive(shard1, {"shard_output-1.json": b'{"i": 1}'})

    # Two completion passes (either order) unpacking against one job dir.
    unpack_agent_result(shard0, job_dir, ("shard_output-0.json",))
    unpack_agent_result(shard1, job_dir, ("shard_output-1.json",))

    # Disjoint per-index names: both survive, neither clobbers the other.
    assert json.loads((job_dir / "shard_output-0.json").read_text()) == {"i": 0}
    assert json.loads((job_dir / "shard_output-1.json").read_text()) == {"i": 1}
    # The ordinary output the node declares is not part of any shard's
    # contract — nothing promoted it into the shared job dir.
    assert not (job_dir / "out.json").exists()


def test_shard_manifest_expected_outputs_exclude_ordinary_outputs(job_db, tmp_path: Path) -> None:
    """#401 review P1-2 (dispatch side): a shard manifest's expected_outputs
    is exactly [shard_output-N.json] — the node's ordinary outputs are
    excluded. Covers the persisted manifest (remote contract); the local
    lane mirrors it in shard_dispatch."""
    from server.app.agent_broker import AgentExecutionBroker
    from server.app.agent_broker.code_dispatch import CodeDispatchService
    from server.app.executors.contracts import CodeCapabilityConfig
    from server.app.services.artifact_store import ArtifactStore
    from server.app.settings import Settings
    from server.app.workflows.definition import WorkflowNode
    from tests.postgres_support import TEST_DATABASE_URL

    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-p12', 'Test', 'demo_workflow') on conflict do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('job-p12', 'ws-p12', 'question', 'job-p12')"
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('job-p12', 'package')")
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=tmp_path, bundle_dir=tmp_path / "bundles"
    )
    repo_root = Path(__file__).resolve().parents[2]
    settings = Settings(
        root_dir=repo_root,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        database_url=TEST_DATABASE_URL,
    )
    service = CodeDispatchService(
        settings, broker, ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL), job_db
    )
    node = WorkflowNode(
        key="package", label="package", capability="package", outputs=["out.json", "extra.json"]
    )

    queued = service.enqueue(
        capability="package",
        capability_config=CodeCapabilityConfig(),
        workspace={"id": "ws-p12"},
        job={"id": "job-p12"},
        workflow_key="questions",
        node=node,
        job_dir=tmp_path / "job",
        log_path=tmp_path / "logs" / "jobs" / "job-p12-package.log",
        inputs=(),
        code_text="def run(job, job_dir, runtime):\n    pass\n",
        custom_code=False,
        config={},
        secret_config={},
        shard_runtime={"shard_index": 7, "shard_input": {"q": 1}},
    )

    assert queued is True
    with job_db._connect_read() as conn:
        row = conn.execute("select manifest_json from agent_execution_requests").fetchone()
    manifest = json.loads(row["manifest_json"])
    # The shard's product contract is ONLY the per-index file.
    assert manifest["expected_outputs"] == ["shard_output-7.json"]
    assert "out.json" not in manifest["expected_outputs"]
    assert "extra.json" not in manifest["expected_outputs"]


def test_cleanup_bundle_on_error_deletes_file_and_reraises_original(tmp_path: Path) -> None:
    """#204 审定保留：补偿-裸-re-raise（#233 同款）——with 块抛错时半建的
    bundle 文件被删除，且原始异常类型原样上抛（不被转换/吞没）。"""

    class ExpectedRefusal(Exception):
        pass

    bundle_path = tmp_path / "bundles" / "exec-1.tar.gz"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_bytes(b"partial bundle")

    with (
        pytest.raises(ExpectedRefusal, match="enqueue refused"),
        cleanup_bundle_on_error(bundle_path),
    ):
        raise ExpectedRefusal("enqueue refused")

    assert not bundle_path.exists()


def test_cleanup_bundle_on_error_keeps_file_on_success(tmp_path: Path) -> None:
    """#204 审定保留：成功路径不动 bundle 文件（只有失败才清理）。"""
    bundle_path = tmp_path / "bundles" / "exec-2.tar.gz"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_bytes(b"final bundle")

    with cleanup_bundle_on_error(bundle_path):
        pass

    assert bundle_path.read_bytes() == b"final bundle"
