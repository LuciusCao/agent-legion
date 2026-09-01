"""Agent Worker 启动预检（worker/runtime/preflight.py）与二进制解析测试。

issue #254 起 agent runtime 声明由本机探测推导（探测即默认启用，
disabled_runtimes 反选停用），「声明了 runtime 但二进制缺失」的错误类
已结构性消除；预检只剩 code 执行容量的 velites 守卫。探测本身由
tests/workers/test_runtime_catalog.py 覆盖。
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

from worker import binary_resolution
from worker import executor as agent_worker
from worker.binary_resolution import resolve_binary
from worker.runtime import setup as runtime_setup
from worker.runtime.preflight import preflight_error

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。"""
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bin")


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _all_missing(_binary: str) -> None:
    return None


@pytest.mark.no_db
def test_prepare_runtime_models_injects_effective_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    effective = [{"runtime": "velites", "provider": "sqai", "model": "kimi"}]
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: (effective, {}),
    )
    config = {"runtimes": ["velites"], "models": []}

    assert runtime_setup.prepare_runtime_models(config) is None
    assert config["models"] == effective


def test_main_refuses_start_when_code_capacity_lacks_velites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """code 容量 > 0 但 velites 不可解析 → 退出码 2（不自动重启），不进入注册。"""
    monkeypatch.setattr(shutil, "which", _all_missing)
    token_file = tmp_path / "register_token"
    token_file.write_text("management-token", encoding="utf-8")
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "http://unused",
                "worker_id": "w1",
                "runtimes": [],
                "max_concurrency": 1,
                "max_code_concurrency": 2,
                "register_token_file": str(token_file),
                "work_root": str(tmp_path / "work"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["agent_worker.py", "--config", str(config_path)])

    assert agent_worker.main() == 2

    out = capsys.readouterr().out
    assert "启动预检失败" in out
    assert "max_code_concurrency" in out
    assert "PATH" in out


@pytest.mark.no_db
def test_preflight_code_capacity_requires_velites(monkeypatch: pytest.MonkeyPatch) -> None:
    # 批次 2：max_code_concurrency > 0 时 code 任务统一经 velites sandbox
    # wrap 执行（fail-closed），与 velites agent runtime 是否启用无关。
    monkeypatch.setattr(shutil, "which", _all_missing)
    error = preflight_error(code_concurrency=2)
    assert error is not None
    assert "max_code_concurrency" in error
    assert "'velites'" in error
    assert "PATH" in error


@pytest.mark.no_db
def test_preflight_code_capacity_passes_with_velites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    assert preflight_error(code_concurrency=2) is None
    # 0 = 仅 agent，不要求 velites。
    monkeypatch.setattr(shutil, "which", _all_missing)
    assert preflight_error(code_concurrency=0) is None


@pytest.mark.no_db
def test_preflight_ignores_agent_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    # issue #254：agent runtime 缺失不再是预检错误（缺失即不声明）。
    monkeypatch.setattr(shutil, "which", _all_missing)
    assert preflight_error() is None


@pytest.mark.no_db
def test_resolve_binary_prefers_bundled_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    assert resolve_binary("velites") == str(bundled_dir / "velites")


@pytest.mark.no_db
def test_resolve_binary_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # autouse fixture 已把自带目录指向不存在的位置。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    assert resolve_binary("velites") == "/usr/local/bin/velites"


@pytest.mark.no_db
def test_resolve_binary_skips_non_executable_bundled_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled_dir = tmp_path / "bundle"
    bundled_dir.mkdir()
    (bundled_dir / "velites").write_text("#!/bin/sh\n", encoding="utf-8")  # 无 +x
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    assert resolve_binary("velites") == "/usr/local/bin/velites"


@pytest.mark.no_db
def test_resolve_binary_missing_everywhere_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _all_missing)
    assert resolve_binary("velites") is None


@pytest.mark.no_db
def test_preflight_code_capacity_passes_with_bundled_velites_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PATH 全空，仅自带副本存在：code 容量预检必须放行（自带沙箱部署路径）。
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", _all_missing)

    assert preflight_error(code_concurrency=2) is None


@pytest.mark.no_db
def test_preflight_code_capacity_error_names_bundled_dir_and_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _all_missing)
    error = preflight_error(code_concurrency=2)
    assert error is not None
    assert str(binary_resolution.BUNDLED_BINARY_DIR) in error  # 自带目录位置
    assert "ensure-velites.sh --dest data/bin" in error  # 安置命令指引


@pytest.mark.no_db
def test_shipped_ui_renders_runtime_status_list() -> None:
    """worker/ui 提供 Agent 运行时状态列表容器（替代旧 opt-in checkbox）。"""
    html = (ROOT / "worker" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="runtime-list"' in html
    assert 'name="runtimes"' not in html
