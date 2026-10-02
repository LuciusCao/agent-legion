"""#828/#830 回归：Host 侧输出校验的读视图必须包含节点声明 inputs。

#759 staging 化把校验对象从 job_dir 换成读视图后，视图只收本次产物，
声明 inputs（上游节点产出、经各自校验的可信字节）从校验面消失——做跨
文件事实回引对账的 skill validator（读上游产物/intake 物化文件）全量
失败。修复：校验前把 manifest 声明的 inputs 从 job_dir 链入视图；与
expected 同名的名仍不链（#779 终审 P1 的残留排除语义不动，由第二个
用例钉住）。链接机制的单元钉在 tests/services/test_completion_staged_view.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.app.agent_control.completion import AgentOutcome
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.skills.commit_cache import NullSkillStore
from server.app.skills.manager import SkillManager
from tests.db.completion_helpers import (
    _completion_handler,
    _node_error,
    _node_row,
    _result_archive,
    _seed_completion_job,
)
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL


def _skill_manager(tmp_path: Path) -> SkillManager:
    return SkillManager(
        store=NullSkillStore(),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "skill-runs",
        git_command=["git"],
    )


def test_validation_view_includes_declared_inputs(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主回归（#828/#830）：声明 input 在 job_dir、归档只带输出——校验时刻
    视图必须同时含 input 字节与本次输出，节点 completed；input 不进
    produced 面（零镜像、零清单行、零落盘移动）。"""
    _seed_completion_job(job_db, workspace_id="inp1-ws", job_id="inp1-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(
        job_db, tmp_path, storage, skill_manager=_skill_manager(tmp_path)
    )
    job_dir = jobs_dir / "inp1-ws" / "inp1-job"
    job_dir.mkdir(parents=True)
    (job_dir / "cleaned_question.json").write_bytes(b'{"q": 1}')
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b'{"fresh": true}'})
    captured: dict[str, bytes] = {}

    def _fake_validate(_sm: Any, _manifest: dict[str, Any], view_dir: Path) -> str | None:
        captured.update(
            {
                p.relative_to(view_dir).as_posix(): p.read_bytes()
                for p in view_dir.rglob("*")
                if p.is_file()
            }
        )
        return None

    monkeypatch.setattr(
        "server.app.agent_control.completion_staged.validate_worker_outputs", _fake_validate
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="inp1-job",
        node_key="node_a",
        manifest={
            "expected_outputs": ["out.json"],
            "inputs": ["cleaned_question.json"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={"out.json": "sha256:deadbeef"},
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("inp1-job", "node_a")["status"] == "completed"
    # 校验时刻：视图同时含声明 input 与本次输出。
    assert captured["cleaned_question.json"] == b'{"q": 1}'
    assert captured["out.json"] == b'{"fresh": true}'
    # input 不进 produced 面：零镜像、零清单行；job_dir 里的 input 原样保留。
    assert "jobs/inp1-ws/inp1-job/cleaned_question.json" not in storage.objects
    assert store.row_for_node("inp1-job", "node_a", "cleaned_question.json") is None
    assert (job_dir / "cleaned_question.json").read_bytes() == b'{"q": 1}'
    assert storage.objects["jobs/inp1-ws/inp1-job/out.json"] == b'{"fresh": true}'


def test_declared_input_never_backfills_expected_output(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#779 终审 P1 保持关闭：input 与 expected 同名（pass-through 形态）且
    本次未上报——job_dir 残留不经 input 通道补齐 produced，仍判 missing
    翻 failed，残留字节原样保留、零镜像零清单。"""
    _seed_completion_job(job_db, workspace_id="inp2-ws", job_id="inp2-job")
    storage = FakeObjectStorage()
    handler, store, jobs_dir = _completion_handler(
        job_db, tmp_path, storage, skill_manager=_skill_manager(tmp_path)
    )
    job_dir = jobs_dir / "inp2-ws" / "inp2-job"
    job_dir.mkdir(parents=True)
    (job_dir / "b.json").write_bytes(b"stale-leftover")  # 旧 attempt 同名残留
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"a.json": b"fresh-a"})
    validated: list[Path] = []
    monkeypatch.setattr(
        "server.app.agent_control.completion_staged.validate_worker_outputs",
        lambda _sm, _manifest, view_dir: validated.append(view_dir) and None,
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="inp2-job",
        node_key="node_a",
        manifest={
            "expected_outputs": ["a.json", "b.json"],
            "inputs": ["b.json"],
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            output_artifacts={"a.json": "sha256:deadbeef"},
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("inp2-job", "node_a")["status"] == "failed"
    assert "Missing outputs: b.json" in _node_error("inp2-job", "node_a")
    assert validated == []  # missing 判定先于校验，校验不跑
    assert (job_dir / "b.json").read_bytes() == b"stale-leftover"
    assert store.row_for_node("inp2-job", "node_a", "b.json") is None
    assert "jobs/inp2-ws/inp2-job/b.json" not in storage.objects


def test_validation_uses_dispatch_frozen_input_bytes(
    job_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex 对抗复审 P1：dispatch 时 stage_agent_inputs 把 Worker 实际消费
    的 input 字节冻结进 CAS（manifest input_artifacts 持 sha256 ref）；之后
    并行生产者覆盖 job_dir 同名文件（job_dir 按 job 共享、多节点同名输出
    受支持）——校验必须对冻结字节做，而不是覆盖后的现场。"""
    _seed_completion_job(job_db, workspace_id="inp3-ws", job_id="inp3-job")
    storage = FakeObjectStorage()
    artifact_store = ArtifactStore(tmp_path / "cas", TEST_DATABASE_URL)
    handler, _store, jobs_dir = _completion_handler(
        job_db, tmp_path, storage, skill_manager=_skill_manager(tmp_path)
    )
    handler.artifact_store = artifact_store  # 真 CAS 替换 stub
    job_dir = jobs_dir / "inp3-ws" / "inp3-job"
    job_dir.mkdir(parents=True)
    # dispatch 时刻：input 在场，字节冻结进 CAS（stage_agent_inputs 同款）。
    (job_dir / "in.json").write_bytes(b"dispatch-frozen")
    digest = artifact_store.put(b"dispatch-frozen")
    # dispatch→completion 之间：并行节点覆盖了 job_dir 同名文件。
    (job_dir / "in.json").write_bytes(b"overwritten-by-parallel-producer")
    _result_archive(tmp_path / "bundles" / "result.tar.gz", {"out.json": b'{"fresh": true}'})
    staging_key = "jobs-staging/inp3-ws/inp3-job/exec-1/out.json"
    storage.objects[staging_key] = b'{"fresh": true}'
    captured: dict[str, bytes] = {}

    def _fake_validate(_sm: Any, _manifest: dict[str, Any], view_dir: Path) -> str | None:
        captured.update(
            {
                p.relative_to(view_dir).as_posix(): p.read_bytes()
                for p in view_dir.rglob("*")
                if p.is_file()
            }
        )
        return None

    monkeypatch.setattr(
        "server.app.agent_control.completion_staged.validate_worker_outputs", _fake_validate
    )

    ok = handler.finish(
        lease_id="lease-1",
        worker_id="worker-1",
        job_id="inp3-job",
        node_key="node_a",
        manifest={
            "expected_outputs": ["out.json"],
            "inputs": ["in.json"],
            "input_artifacts": {"in.json": f"sha256:{digest}"},
            "execution_id": "exec-1",
        },
        outcome=AgentOutcome(
            status="completed",
            exit_code=0,
            # dict-ref 上报：产物校验走 object_store，真 ArtifactStore 全程
            # 无 add_ref 写入（legacy sha256 字符串 ref 会撞 artifact_refs
            # 的外键）。
            output_artifacts={
                "out.json": {"storage_key": staging_key, "size_bytes": 15, "content_hash": ""}
            },
        ),
        archive_name="result.tar.gz",
    )

    assert ok is True
    assert _node_row("inp3-job", "node_a")["status"] == "completed"
    # 校验看到的是 dispatch 冻结字节，不是被覆盖后的 job_dir 现场。
    assert captured["in.json"] == b"dispatch-frozen"
    assert captured["out.json"] == b'{"fresh": true}'
    assert (job_dir / "in.json").read_bytes() == b"overwritten-by-parallel-producer"  # 现场不动
