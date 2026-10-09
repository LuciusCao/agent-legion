"""#843 PR-2：Worker 产出归档的 v2 路由级 roundtrip。

Worker 写侧（prepare + finalize）产出的**真实归档**（result.json 首成员 +
产物 + run_dir 证据）经真实 result 路由（format 头 + worker token + 租约）
提交：204 落库、outcome_json 的元数据与 Worker 侧 metadata 等价、产物清单
（CAS ref）完整入库、v1 换轨成员不产生。PR-1 的读回路径测试基建
（tests/helpers/agent_worker_api.py）+ Worker 侧的真实准备链在此闭环。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from shared.code_contract import (
    RESULT_METADATA_MEMBER,
    RESULT_OUTPUT_ARTIFACTS_MEMBER,
)
from tests.helpers.agent_worker_api import (
    claim as _claim,
)
from tests.helpers.agent_worker_api import (
    make_app as _make_app,
)
from tests.helpers.agent_worker_api import (
    register as _register,
)
from tests.helpers.agent_worker_api import (
    seed_request as _seed_request,
)
from worker.upload.prepare import prepare_result
from worker.upload.queue import UploadTask
from worker.upload.result_manifest import finalize_result_metadata


def _worker_prepared_archive(work_root: Path, task: UploadTask) -> tuple[dict, bytes]:
    """走 Worker 的真实准备链：prepare（body 归档）+ finalize（result.json
    首成员）——与上传队列 bulk 车道同一函数族，产物引用按 CAS 形态置入。"""
    metadata, archive, _outputs = prepare_result(task)
    metadata["output_artifacts"] = {"output.json": f"sha256:{'a' * 64}"}
    metadata, archive = finalize_result_metadata(task, metadata, archive)
    with tarfile.open(archive) as tar:
        assert tar.getnames()[0] == RESULT_METADATA_MEMBER
    return metadata, archive.read_bytes()


def _seed_artifact_hash(app, digest: str) -> None:
    with app.state.job_db.connect() as conn:
        conn.execute(
            "insert into artifacts(hash, size) values (%s, 1) on conflict(hash) do nothing",
            (digest,),
        )


def _outcome_row(app, execution_id: str) -> tuple[str, dict]:
    with app.state.job_db.connect() as conn:
        row = conn.execute(
            "select state, outcome_json from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"]), json.loads(row["outcome_json"])


def test_worker_archive_roundtrips_through_v2_route(tmp_path: Path) -> None:
    """Worker 准备链产出 → format 头 + 真实路由 → 204 落库：存储的元数据
    （error_message / stderr tail / run_dir / 产物清单）与 Worker 侧终态
    metadata 等价，v1 换轨成员不产生。"""
    work_root = tmp_path / "worker"
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (work_root / "exec-1" / "job" / "output.json").write_text("{}", encoding="utf-8")
    task = UploadTask(
        execution_id="exec-1",
        lease_id="lease-1",
        execution_dir=work_root / "exec-1",
        node_key="node_a",
        status_fields={"node_key": "node_a"},
        kind="process",
        exit_code=0,
        expected_outputs=("output.json",),
        command=("pi",),
    )
    metadata, archive_bytes = _worker_prepared_archive(work_root, task)
    assert RESULT_OUTPUT_ARTIFACTS_MEMBER not in _member_names(archive_bytes)

    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-worker-roundtrip", limit=2)
    _seed_artifact_hash(app, "a" * 64)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result-Format": "2",
            },
            content=archive_bytes,
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == metadata["status"] == "completed"
    assert stored["exit_code"] == metadata["exit_code"]
    assert stored["output_artifacts"] == metadata["output_artifacts"]
    assert stored["run_dir"] == metadata["run_dir"]


def _member_names(raw: bytes) -> list[str]:
    import io

    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        return tar.getnames()


def test_worker_prebuilt_metadata_only_archive_roundtrips(tmp_path: Path) -> None:
    """判败/预构建形态：仅含 result.json 的最小归档照常提交（204 落库，
    failed 判决显式可见）。"""
    from worker.upload.result_metadata import write_metadata_only_archive

    work_root = tmp_path / "worker"
    (work_root / "exec-1").mkdir(parents=True)
    task = UploadTask(
        execution_id="exec-1",
        lease_id="lease-1",
        execution_dir=work_root / "exec-1",
        node_key="node_a",
        status_fields={"node_key": "node_a"},
        kind="prebuilt",
        prebuilt_metadata={"status": "failed", "exit_code": 1, "error_message": "preflight"},
    )
    metadata = dict(task.prebuilt_metadata or {})
    metadata.setdefault("output_artifacts", {})
    archive = work_root / "exec-1" / "result.tar.gz"
    write_metadata_only_archive(archive, metadata)

    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-v2-worker-prebuilt", limit=2)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        claimed = _claim(client, token)
        response = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result-Format": "2",
            },
            content=archive.read_bytes(),
        )
        assert response.status_code == 204, response.text
    state, stored = _outcome_row(app, claimed["execution_id"])
    assert state == "done"
    assert stored["status"] == "failed"
    assert stored["error_message"] == "preflight"
