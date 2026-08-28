"""Unit tests for the Agent Worker execution preparation (worker/execution/prepare.py)."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from server.app.agent_broker.agent_bundle import build_agent_bundle
from worker.execution.prepare import prepare_execution
from worker.upload.queue import PENDING_FILENAME, PendingUploadExists


def _make_bundle(tmp_path: Path, manifest: dict) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / "bundle.tar.gz"
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


class FakeClient:
    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())


def test_prepare_execution_substitutes_prompt_placeholders(tmp_path: Path) -> None:
    manifest = {
        "command_spec": {
            "command": ["pi", "@{prompt_file}"],
            "prompt": "Working directory: {job_dir}\nSkill directory: {skill_dir}\n",
        },
        "input_artifacts": {},
        "expected_outputs": ["output.json"],
        "execution": {"timeout_seconds": 60},
    }
    bundle = _make_bundle(tmp_path, manifest)
    claim = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }
    execution_dir = tmp_path / "exec-1"

    prepare_execution(FakeClient(bundle), claim, execution_dir, threading.Semaphore(1))

    prompt = (execution_dir / "job" / "runs" / "node_a" / "worker" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "{job_dir}" not in prompt
    assert "{skill_dir}" not in prompt
    assert f"Working directory: {execution_dir}/job" in prompt
    assert f"Skill directory: {execution_dir}/bundle/skill" in prompt


class ArtifactClient:
    """Serves the bundle plus per-digest artifact payloads."""

    def __init__(self, bundle: Path, artifacts: dict[str, bytes]) -> None:
        self._bundle = bundle
        self._artifacts = artifacts

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.startswith("/api/artifacts/"):
            destination.write_bytes(self._artifacts[path.rsplit("/", 1)[-1]])
        else:
            destination.write_bytes(self._bundle.read_bytes())


def _manifest(input_artifacts: dict[str, str]) -> dict:
    return {
        "command_spec": {"command": ["pi"], "prompt": "hi"},
        "input_artifacts": input_artifacts,
        "expected_outputs": [],
        "execution": {"timeout_seconds": 60},
    }


def _claim() -> dict:
    return {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/api/agent-executions/exec-1/bundle",
    }


@pytest.mark.parametrize("bad_name", ["../escape.txt", "a/../../escape.txt", "/etc/passwd"])
def test_prepare_execution_rejects_unsafe_artifact_names(tmp_path: Path, bad_name: str) -> None:
    digest = hashlib.sha256(b"payload").hexdigest()
    bundle = _make_bundle(tmp_path, _manifest({bad_name: f"sha256:{digest}"}))

    with pytest.raises(ValueError, match="unsafe input artifact name"):
        prepare_execution(
            ArtifactClient(bundle, {digest: b"payload"}),
            _claim(),
            tmp_path / "exec-1",
            threading.Semaphore(1),
        )

    assert not (tmp_path / "escape.txt").exists()


def test_prepare_execution_verifies_artifact_digest(tmp_path: Path) -> None:
    payload = b"x" * (2 << 20)  # 大于 1MB 分块，覆盖流式 sha256
    digest = hashlib.sha256(payload).hexdigest()
    bundle = _make_bundle(tmp_path, _manifest({"inputs/data.bin": f"sha256:{digest}"}))
    execution_dir = tmp_path / "exec-1"

    prepare_execution(
        ArtifactClient(bundle, {digest: payload}),
        _claim(),
        execution_dir,
        threading.Semaphore(1),
    )

    assert (execution_dir / "job" / "inputs" / "data.bin").read_bytes() == payload


def test_prepare_execution_rejects_digest_mismatch(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, _manifest({"data.bin": f"sha256:{'0' * 64}"}))

    with pytest.raises(RuntimeError, match="artifact digest mismatch"):
        prepare_execution(
            ArtifactClient(bundle, {"0" * 64: b"tampered"}),
            _claim(),
            tmp_path / "exec-1",
            threading.Semaphore(1),
        )


def test_prepare_refuses_dir_with_pending_upload_marker(tmp_path: Path) -> None:
    """#203：marker 属于当前 claim 的 lease 时目录归 UploadQueue 所有，prepare
    不得删除或覆盖（restore() 恢复的 pending 结果可能正排队中）。"""
    execution_dir = tmp_path / "exec-1"
    job_dir = execution_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "output.json").write_text("old result", encoding="utf-8")
    marker = execution_dir / PENDING_FILENAME
    marker.write_text(
        '{"version": 1, "execution_id": "exec-1", "lease_id": "lease-1"}', encoding="utf-8"
    )

    with pytest.raises(PendingUploadExists, match=PENDING_FILENAME):
        prepare_execution(
            FakeClient(_make_bundle(tmp_path, _manifest({}))),
            _claim(),
            execution_dir,
            threading.Semaphore(1),
        )

    # 目录与内容原样保留：marker、已准备的产物都不许动。
    assert marker.is_file()
    assert (job_dir / "output.json").read_text(encoding="utf-8") == "old result"
    # prepare 未写入任何新文件（bundle 下载也未发生）。iterdir 顺序随文件
    # 系统实现而异（CI 的 Linux 与本地 macOS 不同），按集合断言。
    assert {p.name for p in execution_dir.iterdir()} == {"job", PENDING_FILENAME}


def test_prepare_clears_orphan_marker_from_stale_lease(tmp_path: Path) -> None:
    """#203 P1：旧 lease 的孤儿 marker（report 必 409、结果注定投递不进去）
    不得牺牲当前 claim——目录随 stale 一起清掉，本次执行照常准备。claim 每
    次消耗 attempt+1 且 sweeper 超过 requeue_limit 就不再重排，最后一次
    允许的 attempt 不能为过期结果殉葬。"""
    execution_dir = tmp_path / "exec-1"
    job_dir = execution_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "output.json").write_text("dead result", encoding="utf-8")
    marker = execution_dir / PENDING_FILENAME
    marker.write_text(
        '{"version": 1, "execution_id": "exec-1", "lease_id": "lease-old"}', encoding="utf-8"
    )

    prepare_execution(
        FakeClient(_make_bundle(tmp_path, _manifest({}))),
        _claim(),  # lease_id="lease-1" ≠ "lease-old"
        execution_dir,
        threading.Semaphore(1),
    )

    # 孤儿目录被清掉、本次 claim 的 bundle 正常落位。
    assert not marker.exists()
    assert not (job_dir / "output.json").exists()
    assert (execution_dir / "bundle.tar.gz").is_file()


def test_prepare_clears_marker_without_lease_field(tmp_path: Path) -> None:
    """无 lease_id 字段的旧版 marker 一律按孤儿处理（保守方向：宁可清掉重跑，
    也不让无法核对所有权的 marker 无限跳过 claim——attempt 预算会被耗尽）。"""
    execution_dir = tmp_path / "exec-1"
    execution_dir.mkdir(parents=True)
    (execution_dir / PENDING_FILENAME).write_text(
        '{"version": 1, "execution_id": "exec-1"}', encoding="utf-8"
    )

    prepare_execution(
        FakeClient(_make_bundle(tmp_path, _manifest({}))),
        _claim(),
        execution_dir,
        threading.Semaphore(1),
    )

    assert not (execution_dir / PENDING_FILENAME).exists()
    assert (execution_dir / "bundle.tar.gz").is_file()


def test_prepare_replaces_stale_dir_without_marker(tmp_path: Path) -> None:
    """无 marker 的崩溃残留目录仍被清理（既有行为回归，issue #203 语义不动摇）。"""
    stale = tmp_path / "exec-1"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("leftover", encoding="utf-8")

    prepare_execution(
        FakeClient(_make_bundle(tmp_path, _manifest({}))),
        _claim(),
        stale,
        threading.Semaphore(1),
    )

    assert not (stale / "junk").exists()
    assert (stale / "bundle.tar.gz").is_file()
