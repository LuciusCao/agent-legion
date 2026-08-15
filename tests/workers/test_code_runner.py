"""worker/code_runner.py 单测：prepare/hash 校验/scrub/argv/取消/auth marker/结果打包。

批次 2（协议 v2）Worker 执行侧契约测试；与 Host 侧
tests/services/test_code_dispatch.py、tests/routes/test_agent_workers.py
的结果接收测试互为两端。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from worker import binary_resolution
from worker.code_runner import (
    build_sandbox_argv,
    cancel_executions,
    execute_code,
    prepare_code_execution,
    prepare_code_result,
    register_cancellation,
    strip_secret_config,
    unregister_cancellation,
)
from worker.status import ExecutionStatusReporter
from worker.upload_queue import UploadTask

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]

CODE_OK = (
    "from pathlib import Path\n"
    "\n"
    "def run(job, job_dir, runtime):\n"
    '    Path(job_dir, "output.json").write_text("{}", encoding="utf-8")\n'
)

CODE_AUTH_FAILURE = (
    "from pathlib import Path\n"
    "from workspace_libs.node_sdk import NodeContext\n"
    "\n"
    "def run(job, job_dir, runtime):\n"
    "    ctx = NodeContext(job, Path(job_dir), runtime)\n"
    "    ctx.report_auth_failure()\n"
    '    Path(job_dir, "output.json").write_text("{}", encoding="utf-8")\n'
)

CODE_SLEEPER = "import time\n\ndef run(job, job_dir, runtime):\n    time.sleep(60)\n"


def _code_bundle(tmp_path: Path, code_text: str = CODE_OK) -> Path:
    """与 server code_dispatch.build_code_bundle 同布局（故意无 manifest.json）。"""
    bundle = tmp_path / "code-bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(
            REPO_ROOT / "workspace_libs",
            arcname="workspace_libs",
            filter=lambda member: None if "__pycache__" in member.name else member,
        )
        data = code_text.encode("utf-8")
        info = tarfile.TarInfo("node_code.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return bundle


def _code_manifest(code_text: str = CODE_OK, **overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "kind": "code",
        "execution_id": "exec-code-1",
        "workspace_id": "ws-1",
        "job_id": "job-1",
        "workflow_key": "wf",
        "node_key": "node_a",
        "capability": "intake_items",
        "code_hash": hashlib.sha256(code_text.encode("utf-8")).hexdigest(),
        "custom_code": False,
        "config_schema": {},
        "config": {},
        "inputs": [],
        "expected_outputs": ["output.json"],
        "timeout_seconds": 30,
        "sandbox_network": False,
        "log_path": "data/logs/jobs/job-1-node_a.log",
        "runtime_context": {
            "job": {"id": "job-1", "workflow_key": "wf"},
            "workspace": {"id": "ws-1"},
            "settings_config": {},
            "job_batch": None,
            "skill_versions": {},
        },
    }
    manifest.update(overrides)
    return manifest


def _code_claim(bundle_url: str = "/bundle/x.tar.gz", **manifest_overrides: Any) -> dict[str, Any]:
    code_text = manifest_overrides.pop("code_text", CODE_OK)
    return {
        "execution_id": "exec-code-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "kind": "code",
        "bundle_url": bundle_url,
        "manifest": _code_manifest(code_text, **manifest_overrides),
    }


class FakeClient:
    """下载桩：bundle_url 返回 bundle 字节，/api/artifacts/<digest> 返回登记内容。"""

    def __init__(self, bundle: Path, artifacts: dict[str, bytes] | None = None) -> None:
        self._bundle = bundle
        self._artifacts = artifacts or {}

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.startswith("/api/artifacts/"):
            destination.write_bytes(self._artifacts[path.rsplit("/", 1)[-1]])
        else:
            destination.write_bytes(self._bundle.read_bytes())


def _prepare(tmp_path: Path, claim: dict[str, Any], client: FakeClient) -> Any:
    return prepare_code_execution(
        client, claim, tmp_path / "work" / "exec-code-1", threading.Semaphore(2)
    )


def test_prepare_stages_code_snapshot_and_inputs(tmp_path: Path) -> None:
    payload = b'{"row": 1}'
    digest = hashlib.sha256(payload).hexdigest()
    claim = _code_claim(input_artifacts={"inputs/in.json": f"sha256:{digest}"})
    client = FakeClient(_code_bundle(tmp_path), {digest: payload})

    prepared = _prepare(tmp_path, claim, client)

    execution_dir = tmp_path / "work" / "exec-code-1"
    assert (execution_dir / "bundle" / "node_code.py").is_file()
    # workspace_libs 快照随 bundle 落 staging（child 的 import 根）。
    assert (execution_dir / "bundle" / "workspace_libs" / "code_child.py").is_file()
    assert (execution_dir / "job" / "inputs" / "in.json").read_bytes() == payload
    assert prepared.code_text == CODE_OK
    # bundle 故意不含 manifest.json：唯一权威是 claim 响应。
    assert not (execution_dir / "bundle" / "manifest.json").exists()


def test_prepare_rejects_code_hash_mismatch(tmp_path: Path) -> None:
    claim = _code_claim(code_hash="0" * 64)
    client = FakeClient(_code_bundle(tmp_path))
    with pytest.raises(ValueError, match="code_hash mismatch"):
        _prepare(tmp_path, claim, client)


def test_prepare_rejects_bundle_without_node_code(tmp_path: Path) -> None:
    bundle = tmp_path / "empty.tar.gz"
    with tarfile.open(bundle, "w:gz"):
        pass
    with pytest.raises(ValueError, match="node_code.py"):
        _prepare(tmp_path, _code_claim(), FakeClient(bundle))


def test_prepare_rejects_unsafe_bundle_member(tmp_path: Path) -> None:
    bundle = tmp_path / "evil.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        data = b"evil"
        info = tarfile.TarInfo("../evil.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe Agent bundle member"):
        _prepare(tmp_path, _code_claim(), FakeClient(bundle))


def test_strip_secret_config_drops_secret_keys_and_connection_block() -> None:
    schema = {
        "properties": {
            "token": {"type": "string", "secret": True},
            "threshold": {"type": "number"},
        }
    }
    config = {"token": "s3cr3t", "threshold": 0.5, "connection_config": {"token": "abc"}}
    assert strip_secret_config(config, schema) == {"threshold": 0.5}


def test_build_sandbox_argv_structure() -> None:
    argv = build_sandbox_argv(
        "/usr/bin/velites",
        Path("/work/e1/job"),
        Path("/work/e1/bundle"),
        Path("/work/e1/job/.code_result.json"),
        sandbox_network=True,
    )
    assert argv[:4] == ["/usr/bin/velites", "sandbox", "wrap", "--cwd"]
    separator = argv.index("--")
    assert argv[separator + 1 : separator + 4][1:] == ["-m", "workspace_libs.code_child"]
    wrapped = argv[:separator]
    assert "--allow-network" in wrapped
    # 读白名单含 bundle 快照根（node_code.py + workspace_libs）。
    allow_reads = [wrapped[i + 1] for i, tok in enumerate(wrapped) if tok == "--allow-read"]
    assert "/work/e1/bundle" in allow_reads

    no_network = build_sandbox_argv(
        "/usr/bin/velites",
        Path("/work/e1/job"),
        Path("/work/e1/bundle"),
        Path("/work/e1/job/.code_result.json"),
        sandbox_network=False,
    )
    assert "--allow-network" not in no_network


def _fake_velites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """velites 桩：跳过 wrap 参数直接 exec -- 之后的命令（不真实沙箱）。"""
    script = tmp_path / "velites"
    script.write_text(
        '#!/usr/bin/env bash\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    # 自带目录指向不存在的位置：测试不依赖开发机 data/bin 的真实状态。
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(
        shutil, "which", lambda binary: str(script) if binary == "velites" else None
    )


def _execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim: dict[str, Any],
    client: FakeClient,
    shutdown: threading.Event | None = None,
) -> UploadTask | None:
    _fake_velites(tmp_path, monkeypatch)
    return execute_code(
        client,
        claim,
        tmp_path / "work" / str(claim["execution_id"]),
        {"node_key": "node_a"},
        threading.Semaphore(2),
        shutdown or threading.Event(),
        1,
        threading.Event(),
        SimpleNamespace(proc_ref={}),  # type: ignore[arg-type]
        ExecutionStatusReporter(None),
    )


def test_execute_code_completed_and_result_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(_code_bundle(tmp_path))
    task = _execute(tmp_path, monkeypatch, _code_claim(), client)

    assert task is not None
    assert task.exec_kind == "code"
    assert task.code_result is not None
    assert task.code_result["status"] == "completed"
    execution_dir = tmp_path / "work" / "exec-code-1"
    assert (execution_dir / "job" / "output.json").is_file()
    node_log = execution_dir / "node.log"
    assert node_log.is_file()
    # child 的 stdout/stderr（logging）捕获为 node.log。
    assert "node_a" in node_log.read_text(encoding="utf-8")

    metadata, archive, outputs = prepare_code_result(task)
    assert metadata["status"] == "completed"
    assert metadata["exit_code"] == 0
    assert "auth_failure_connection" not in metadata
    assert outputs == ["output.json"]
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    # 归档契约：expected_outputs 按 job-dir 相对名 + 根部 node.log。
    assert "output.json" in names
    assert "node.log" in names


def test_execute_code_reports_auth_failure_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(_code_bundle(tmp_path, CODE_AUTH_FAILURE))
    claim = _code_claim(code_text=CODE_AUTH_FAILURE, config={"connection": "cms-main"})
    task = _execute(tmp_path, monkeypatch, claim, client)

    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed"
    assert task.code_result["auth_failure_connection"] == "cms-main"
    metadata, _, _ = prepare_code_result(task)
    assert metadata["auth_failure_connection"] == "cms-main"


def test_execute_code_hash_mismatch_refuses_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_velites(tmp_path, monkeypatch)
    client = FakeClient(_code_bundle(tmp_path))
    with pytest.raises(ValueError, match="code_hash mismatch"):
        execute_code(
            client,
            _code_claim(code_hash="0" * 64),
            tmp_path / "work" / "exec-code-1",
            {"node_key": "node_a"},
            threading.Semaphore(2),
            threading.Event(),
            1,
            threading.Event(),
            SimpleNamespace(proc_ref={}),  # type: ignore[arg-type]
            ExecutionStatusReporter(None),
        )


def test_execute_code_uses_bundled_velites_when_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自带副本优先且不依赖 PATH：PATH 全空时执行走 data/bin 的 velites。"""
    bundled_dir = tmp_path / "data" / "bin"
    bundled_dir.mkdir(parents=True)
    stub = bundled_dir / "velites"
    stub.write_text(
        '#!/usr/bin/env bash\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    client = FakeClient(_code_bundle(tmp_path))
    task = execute_code(
        client,
        _code_claim(),
        tmp_path / "work" / "exec-code-1",
        {"node_key": "node_a"},
        threading.Semaphore(2),
        threading.Event(),
        1,
        threading.Event(),
        SimpleNamespace(proc_ref={}),  # type: ignore[arg-type]
        ExecutionStatusReporter(None),
    )

    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed"
    assert task.command[0] == str(stub)


def test_execute_code_fails_closed_without_any_velites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自带副本与 PATH 都找不到 velites → 拒绝执行（EXEC-CODE-003 fail-closed）。"""
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(shutil, "which", lambda _binary: None)
    client = FakeClient(_code_bundle(tmp_path))
    with pytest.raises(RuntimeError, match="refusing to run unsandboxed"):
        execute_code(
            client,
            _code_claim(),
            tmp_path / "work" / "exec-code-1",
            {"node_key": "node_a"},
            threading.Semaphore(2),
            threading.Event(),
            1,
            threading.Event(),
            SimpleNamespace(proc_ref={}),  # type: ignore[arg-type]
            ExecutionStatusReporter(None),
        )


def test_execute_code_cancel_kills_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(_code_bundle(tmp_path, CODE_SLEEPER))
    claim = _code_claim(code_text=CODE_SLEEPER)
    execution_dir = tmp_path / "work" / "exec-code-1"
    result: list[UploadTask | None] = []
    thread = threading.Thread(
        target=lambda: result.append(_execute(tmp_path, monkeypatch, claim, client)),
        daemon=True,
    )
    thread.start()
    # 等子进程起来（pgid 记录落盘）再模拟 Host 心跳取消。
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (execution_dir / "agent_pgid").exists():
        time.sleep(0.02)
    started = time.monotonic()
    assert cancel_executions(["exec-code-1"]) == 1
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert time.monotonic() - started < 15, "Host 取消未及时 kill 进程组"
    assert result[0] is not None and result[0].code_result is not None
    assert result[0].code_result["status"] == "cancelled"
    # 取消的执行也上传 node.log（批次 2 决策 10）。
    _, archive, _ = prepare_code_result(result[0])
    with tarfile.open(archive, "r:gz") as tar:
        assert "node.log" in tar.getnames()


def test_execute_code_timeout_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(_code_bundle(tmp_path, CODE_SLEEPER))
    claim = _code_claim(code_text=CODE_SLEEPER, timeout_seconds=1)
    started = time.monotonic()
    task = _execute(tmp_path, monkeypatch, claim, client)
    assert time.monotonic() - started < 15
    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "failed"
    assert "timed out" in task.code_result["error_message"]
    assert task.exit_code == 124


def test_secrets_stay_off_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """含 secret 的 manifest 只存内存 + stdin：执行目录任何文件都不得出现。"""
    schema = {"properties": {"token": {"type": "string", "secret": True}}}
    config = {"token": "s3cr3t-value-xyz", "threshold": 0.5}
    client = FakeClient(_code_bundle(tmp_path))
    claim = _code_claim(config_schema=schema, config=config)
    task = _execute(tmp_path, monkeypatch, claim, client)
    assert task is not None and task.code_result is not None
    assert task.code_result["status"] == "completed"

    # pending marker 持久化也不含 secret（exec_kind/code_result 随 marker 落盘）。
    assert "s3cr3t-value-xyz" not in json.dumps(task.to_json())
    for path in (tmp_path / "work" / "exec-code-1").rglob("*"):
        if path.is_file():
            assert b"s3cr3t-value-xyz" not in path.read_bytes(), f"secret leaked into {path}"


def test_cancel_executions_only_matches_registered() -> None:
    event = register_cancellation("exec-a")
    try:
        assert cancel_executions(["exec-a", "exec-b"]) == 1
        assert event.is_set()
    finally:
        unregister_cancellation("exec-a")
    assert cancel_executions(["exec-a"]) == 0
