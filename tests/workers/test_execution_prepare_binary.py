"""prepare_execution 的 argv[0] 二进制解析（worker/execution/prepare.py）。

启动预检（runtime_preflight）放行「自带副本或 PATH 可解析」的 runtime，
spawn 侧必须解析到同一个二进制，否则预检通过、claim 后 spawn 即失败。
"""

from __future__ import annotations

import shutil
import stat
import threading
from pathlib import Path

import pytest

from server.app.agent_broker.agent_bundle import build_agent_bundle
from worker import binary_resolution
from worker.execution.prepare import prepare_execution

pytestmark = pytest.mark.no_db


def _make_bundle(tmp_path: Path, command: list[str]) -> Path:
    skill_src = tmp_path / "skill_src"
    skill_src.mkdir(exist_ok=True)
    (skill_src / "SKILL.md").write_text("# s", encoding="utf-8")
    bundle = tmp_path / "bundle.tar.gz"
    manifest = {
        "command_spec": {"command": command, "prompt": "hi"},
        "input_artifacts": {},
        "expected_outputs": [],
        "execution": {"timeout_seconds": 60},
    }
    build_agent_bundle(bundle, skill_dir=skill_src, manifest=manifest)
    return bundle


class FakeClient:
    def __init__(self, bundle: Path) -> None:
        self._bundle = bundle

    def download(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._bundle.read_bytes())


def _prepare(tmp_path: Path, command: list[str]) -> list[str]:
    claim = {
        "execution_id": "exec-1",
        "lease_id": "lease-1",
        "node_key": "node_a",
        "bundle_url": "/bundle/x.tar.gz",
    }
    prepared = prepare_execution(
        FakeClient(_make_bundle(tmp_path, command)),
        claim,
        tmp_path / "exec-1",
        threading.Semaphore(1),
    )
    return prepared.command


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_argv0_resolves_to_bundled_copy_when_path_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "data" / "bin" / "velites"
    _write_executable(bundled)
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled.parent)
    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    command = _prepare(tmp_path, ["velites", "run", "--x"])

    assert command[0] == str(bundled)
    assert command[1:] == ["run", "--x"]


def test_argv0_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    command = _prepare(tmp_path, ["velites", "run"])

    assert command[0] == "/usr/local/bin/velites"


def test_argv0_left_untouched_when_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 无法解析时保持原名（spawn 会报 FileNotFoundError 并作为失败执行上报）；
    # 绝对路径 argv[0] 不参与解析。
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bundled-bin")
    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    assert _prepare(tmp_path, ["velites"])[0] == "velites"
    assert _prepare(tmp_path, ["/opt/custom/bin/runtime"])[0] == "/opt/custom/bin/runtime"
