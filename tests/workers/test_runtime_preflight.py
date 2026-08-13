"""Agent Worker 启动预检（worker/runtime_preflight.py）与 runtime 声明一致性测试。"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from worker import executor as agent_worker
from worker.runtime_preflight import preflight_error

ROOT = Path(__file__).resolve().parents[2]


def _all_missing(_binary: str) -> None:
    return None


@pytest.mark.no_db
def test_preflight_rejects_missing_binary_with_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _all_missing)

    error = preflight_error(["pi", "velites"])

    assert error is not None
    assert "运行时 'velites'" in error  # 缺哪个 runtime
    assert "可执行文件 'velites'" in error  # 缺哪个二进制
    assert "PATH" in error
    assert "移除" in error  # 修复指引


@pytest.mark.no_db
def test_preflight_passes_when_binaries_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    assert preflight_error(["pi", "velites"]) is None


@pytest.mark.no_db
def test_preflight_pi_requires_pi_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime 钉死二进制（phase 3）：runtime: pi 的 argv[0] 恒为 pi，只装
    # velites 的 Worker 声明 pi 必须被拒。
    monkeypatch.setattr(
        shutil, "which", lambda binary: "/usr/local/bin/velites" if binary == "velites" else None
    )
    error = preflight_error(["pi"])
    assert error is not None
    assert "运行时 'pi'" in error


@pytest.mark.no_db
def test_preflight_velites_requires_velites_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime: velites 钉死 velites 实现：只有 pi 没有 velites 必须拒绝。
    monkeypatch.setattr(
        shutil, "which", lambda binary: "/usr/local/bin/pi" if binary == "pi" else None
    )
    error = preflight_error(["velites"])
    assert error is not None
    assert "运行时 'velites'" in error


@pytest.mark.no_db
def test_preflight_does_not_probe_openclaw(monkeypatch: pytest.MonkeyPatch) -> None:
    # openclaw dispatch 本就 fail-fast，不存在 claim 后 spawn 的路径，不探测。
    monkeypatch.setattr(shutil, "which", _all_missing)
    assert preflight_error(["openclaw"]) is None


@pytest.mark.no_db
def test_preflight_default_pi_declaration_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda binary: "/usr/local/bin/pi" if binary == "pi" else None
    )
    assert preflight_error(["pi"]) is None


def test_main_refuses_start_when_declared_runtime_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """声明了 PATH 上没有二进制的 runtime → 退出码 2（不自动重启），不进入注册。"""
    monkeypatch.setattr(shutil, "which", _all_missing)
    token_file = tmp_path / "register_token"
    token_file.write_text("management-token", encoding="utf-8")
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "http://unused",
                "worker_id": "w1",
                "runtimes": ["pi", "velites"],
                "max_concurrency": 1,
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
    assert "运行时 'velites'" in out
    assert "PATH" in out


@pytest.mark.no_db
def test_preflight_code_capacity_requires_velites(monkeypatch: pytest.MonkeyPatch) -> None:
    # 批次 2：max_code_concurrency > 0 时 code 任务统一经 velites sandbox
    # wrap 执行（fail-closed），即使未声明 velites runtime 也必须有二进制。
    monkeypatch.setattr(
        shutil, "which", lambda binary: "/usr/local/bin/pi" if binary == "pi" else None
    )
    error = preflight_error(["pi"], code_concurrency=2)
    assert error is not None
    assert "max_code_concurrency" in error
    assert "'velites'" in error
    assert "PATH" in error


@pytest.mark.no_db
def test_preflight_code_capacity_passes_with_velites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    assert preflight_error(["pi"], code_concurrency=2) is None
    # 0 = 仅 agent，不要求 velites。
    monkeypatch.setattr(
        shutil, "which", lambda binary: "/usr/local/bin/pi" if binary == "pi" else None
    )
    assert preflight_error(["pi"], code_concurrency=0) is None


@pytest.mark.no_db
def test_shipped_ui_offers_checkbox_for_every_supported_runtime() -> None:
    """worker/ui 的 runtime checkbox 集合与 validate_config 白名单一致。"""
    html = (ROOT / "worker" / "ui" / "index.html").read_text(encoding="utf-8")
    offered = sorted(set(re.findall(r'name="runtimes"[^>]*value="([^"]+)"', html)))
    assert offered == ["openclaw", "pi", "velites"]
