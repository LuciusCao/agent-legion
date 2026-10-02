"""#755 对抗复审 P2-1：CAS 最后手段截断形态的 Host 完成判定。

Worker 结果头超预算时 CAS 形态的产物清单被整体降级为空并打
``output_artifacts_truncated`` 标记——产物字节本来就在归档里。Host 见
truncated 标记跳过「空清单改判 failed」，改从归档暂存视图判定
produced/missing；标记缺失的旧形态行为不变。

种子/handler 装配复用 tests/db/completion_helpers.py。
"""

from __future__ import annotations

from pathlib import Path

from server.app.agent_control.completion import AgentOutcome
from server.app.jobs import JobQueries
from tests.db.completion_helpers import (
    _completion_handler,
    _node_error,
    _node_row,
    _result_archive,
    _seed_completion_job,
)
from tests.fakes.storage import FakeObjectStorage


def test_completion_truncated_manifest_judged_from_archive_view(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """completed + expected 非空 + 头部清单为空 + truncated 标记：不再改判
    failed——产物从归档暂存视图判定（字节本来就在归档里），照常提升、镜像、
    登记清单行。"""
    _seed_completion_job(job_db, workspace_id="trunc1-ws", job_id="trunc1-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "trunc1-ws" / "trunc1-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b'{"ok": true}'})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="trunc1-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={},
            output_artifacts_truncated=True,
            output_artifacts_total=1,
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("trunc1-job", "node_a")["status"] == "completed"
    assert (job_dir / "out.json").read_bytes() == b'{"ok": true}'
    assert store.row_for_node("trunc1-job", "node_a", "out.json") is not None
    assert storage.objects["jobs/trunc1-ws/trunc1-job/out.json"] == b'{"ok": true}'


def test_completion_truncated_manifest_missing_output_still_fails(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """对照：truncated 标记不豁免 produced/missing 检查——归档里也没有
    expected 产物时仍判 Missing outputs 翻 failed。"""
    _seed_completion_job(job_db, workspace_id="trunc2-ws", job_id="trunc2-job")
    storage = FakeObjectStorage()
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "trunc2-ws" / "trunc2-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"node.log": b"log"})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="trunc2-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={},
            output_artifacts_truncated=True,
            output_artifacts_total=1,
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("trunc2-job", "node_a")["status"] == "failed"
    assert "Missing outputs: out.json" in _node_error("trunc2-job", "node_a")


def test_completion_empty_manifest_without_truncation_marker_still_fails(
    job_db: JobQueries, tmp_path: Path
) -> None:
    """旧行为不变：缺 truncated 标记的空清单（旧 Worker / 未触发预算的真空
    洞）仍按「did not report output artifacts」改判 failed——即使归档里
    恰好有同名文件。"""
    _seed_completion_job(job_db, workspace_id="trunc3-ws", job_id="trunc3-job")
    storage = FakeObjectStorage()
    handler, _store, jobs_dir = _completion_handler(job_db, tmp_path, storage)
    job_dir = jobs_dir / "trunc3-ws" / "trunc3-job"
    job_dir.mkdir(parents=True)
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b'{"ok": true}'})

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="trunc3-job",
        node_key="node_a",
        manifest={"expected_outputs": ["out.json"], "execution_id": "exec-1"},
        outcome=AgentOutcome(status="completed", exit_code=0, output_artifacts={}),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("trunc3-job", "node_a")["status"] == "failed"
    assert "did not report output artifacts" in _node_error("trunc3-job", "node_a")
