"""Agent Worker 启动预检（worker/runtime/preflight.py）与二进制解析测试。

issue #254 起 agent runtime 声明由本机探测推导（探测即默认启用，
disabled_runtimes 反选停用），「声明了 runtime 但二进制缺失」的错误类
已结构性消除；预检守卫两个维度：code 执行容量的 velites 守卫，以及
#381 起 velites/pi 移出镜像后的期望 runtime 守卫
（AGENT_WORKER_EXPECT_RUNTIMES，专治「docker worker 忘了挂载」的
静默零容量）。探测本身由 tests/workers/test_runtime_catalog.py 覆盖。
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

from shared import code_sandbox
from worker import binary_resolution
from worker import executor as agent_worker
from worker.binary_resolution import resolve_binary
from worker.runtime import setup as runtime_setup
from worker.runtime.preflight import parse_expect_runtimes, preflight_error

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。

    目录常量定义在 shared/code_sandbox.py（BUNDLED_SANDBOX_DIR），
    worker/binary_resolution.py re-export 为 BUNDLED_BINARY_DIR——模块属性
    各自独立，两侧都要 patch，runtime 解析与沙箱解析（#383）才同时隔离。"""
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", tmp_path / "no-bin")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bin")


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
    # 批次 2：max_code_concurrency > 0 时 code 任务统一经沙箱包装器执行
    # （#383 起候选 velites-sandbox → velites），与 agent runtime 无关。
    monkeypatch.setattr(shutil, "which", _all_missing)
    error = preflight_error(code_concurrency=2)
    assert error is not None
    assert "max_code_concurrency" in error
    assert "velites-sandbox" in error
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
    # PATH 全空，仅自带副本存在：code 容量预检必须放行（裸机自带沙箱部署
    # 路径——沙箱解析与 runtime 解析共用 data/bin，两侧常量都指向它）。
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", _all_missing)

    assert preflight_error(code_concurrency=2) is None


@pytest.mark.no_db
def test_preflight_code_capacity_error_names_candidates_and_remedies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _all_missing)
    error = preflight_error(code_concurrency=2)
    assert error is not None
    assert "velites-sandbox" in error  # 候选点名（#383 新 bin）
    assert "velites" in error
    assert "PATH" in error
    # 修复指引：docker 形态指向镜像内置，裸机形态指向构建命令。
    assert "镜像" in error
    assert "ensure-velites.sh" in error


# ---- 期望 runtime 守卫（issue #381：执行器移出镜像后的防漏挂载） ----


@pytest.mark.no_db
def test_parse_expect_runtimes_values() -> None:
    # None / 空白 = 守卫未启用；逗号分隔解析（容忍空白条目）。
    assert parse_expect_runtimes(None) is None
    assert parse_expect_runtimes("") is None
    assert parse_expect_runtimes("   ") is None
    assert parse_expect_runtimes("velites") == ["velites"]
    assert parse_expect_runtimes(" velites , pi ") == ["velites", "pi"]


@pytest.mark.no_db
def test_parse_expect_runtimes_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match="不支持的 runtime"):
        parse_expect_runtimes("velites,openclaw")


@pytest.mark.no_db
def test_preflight_expect_runtimes_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # #381 的核心场景：忘了挂载 velites 的 docker worker，PATH 全空 →
    # fail-fast，错误信息指向挂载/架构排查方向。
    monkeypatch.setattr(shutil, "which", _all_missing)
    error = preflight_error(expect_runtimes=["velites"])
    assert error is not None
    assert "AGENT_WORKER_EXPECT_RUNTIMES" in error
    assert "'velites'" in error
    assert str(binary_resolution.BUNDLED_BINARY_DIR) in error
    assert "架构" in error  # 挂载了错误架构的二进制同样探测不到


@pytest.mark.no_db
def test_preflight_expect_runtimes_partial_missing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 期望 velites+pi、只探测到 velites：缺失的 pi 单独点名。
    monkeypatch.setattr(
        shutil, "which", lambda binary: f"/usr/local/bin/{binary}" if binary == "velites" else None
    )
    error = preflight_error(expect_runtimes=["velites", "pi"])
    assert error is not None
    assert "'pi'" in error
    assert "'velites'" not in error.split("：")[-1]  # 已装的不点名


@pytest.mark.no_db
def test_preflight_expect_runtimes_satisfied_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 自带副本目录（docker 挂载路径）解析到 velites 即满足，无需 PATH。
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(binary_resolution, "BUNDLED_BINARY_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", _all_missing)
    assert preflight_error(expect_runtimes=["velites"]) is None


@pytest.mark.no_db
def test_prepare_runtime_models_reads_expect_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # executor 入口经环境变量接线：声明了探测不到的 runtime → 启动错误。
    monkeypatch.setattr(shutil, "which", _all_missing)
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velites")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: ([], {}),
    )
    config = {"runtimes": [], "models": []}

    error = runtime_setup.prepare_runtime_models(config)

    assert error is not None
    assert "AGENT_WORKER_EXPECT_RUNTIMES" in error
    # 预检失败时不得继续注入发现结果。
    assert config.get("models", []) == []


@pytest.mark.no_db
def test_prepare_runtime_models_invalid_expect_env_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 拼写错误的期望值按部署错误处理（不静默忽略）。
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velite")
    config = {"runtimes": [], "models": []}

    error = runtime_setup.prepare_runtime_models(config)

    assert error is not None
    assert "velite" in error


@pytest.mark.no_db
def test_prepare_runtime_models_expected_runtime_discovery_failure_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # codex P1 on #384：错误架构的二进制通过存在性探测（is_file + X_OK），
    # 执行时才以 exec format error 失败——期望 runtime 的发现失败必须转
    # 启动失败，否则守卫声称覆盖的场景仍退化为静默零容量注册。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velites")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: (
            [],
            {"velites": "[Errno 8] Exec format error: '/usr/local/bin/velites'"},
        ),
    )
    config = {"disabled_runtimes": [], "models": []}

    error = runtime_setup.prepare_runtime_models(config)

    assert error is not None
    assert "期望 runtime" in error
    assert "Exec format error" in error
    assert "架构" in error
    assert config.get("models", []) == []


@pytest.mark.no_db
def test_prepare_runtime_models_unexpected_discovery_failure_stays_soft(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 非期望 runtime 的发现失败维持软告警（不领取该 runtime 的任务即可），
    # 守卫语义只覆盖显式声明的期望集合。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velites")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: (
            [{"runtime": "velites", "provider": "sqai", "model": "kimi"}],
            {"pi": "pi: command failed"},
        ),
    )
    config = {"disabled_runtimes": [], "models": []}

    assert runtime_setup.prepare_runtime_models(config) is None
    assert config["models"] == [{"runtime": "velites", "provider": "sqai", "model": "kimi"}]
    assert "pi" in capsys.readouterr().out


@pytest.mark.no_db
def test_shipped_ui_renders_runtime_status_list() -> None:
    """worker/ui 提供 Agent 运行时状态列表容器（替代旧 opt-in checkbox）。"""
    html = (ROOT / "worker" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="runtime-list"' in html
    assert 'name="runtimes"' not in html


@pytest.mark.no_db
def test_prepare_runtime_models_expect_conflicts_with_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subagent P2-1 on #384：期望 runtime 已安装但被停用 → fail-fast。

    只查「已安装」不查「生效」时，旧版 opt-in `runtimes` 键迁移（catalog
    会把它转成 disabled_runtimes 补集）可让守卫绿灯 + 零 runtime 注册——
    恰是守卫要消灭的静默形态。"""
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velites")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: ([], {}),
    )
    # 旧版 opt-in 键：只启用 pi → 迁移后 disabled_runtimes = [velites]。
    config = {"runtimes": ["pi"], "models": []}

    error = runtime_setup.prepare_runtime_models(config)

    assert error is not None
    assert "disabled_runtimes" in error
    assert "velites" in error
    assert "取消停用" in error  # 两条修正路径都说清楚


@pytest.mark.no_db
def test_prepare_runtime_models_expect_unaffected_by_unrelated_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 期望 velites、停用的是 pi：无冲突，正常启动（软告警路径照旧）。
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    monkeypatch.setenv("AGENT_WORKER_EXPECT_RUNTIMES", "velites")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: (
            [{"runtime": "velites", "provider": "sqai", "model": "kimi"}],
            {},
        ),
    )
    config = {"disabled_runtimes": ["pi"], "models": []}

    assert runtime_setup.prepare_runtime_models(config) is None


@pytest.mark.no_db
def test_parse_expect_runtimes_dedupes() -> None:
    # 重复值去重：错误文案逐项点名，重复会在文案里复读。
    assert parse_expect_runtimes("velites,velites, pi ") == ["velites", "pi"]


@pytest.mark.no_db
def test_compose_files_carry_velites_mount_and_guard() -> None:
    """两个 compose 的 worker 服务必须同步携带 velites 挂载与期望守卫。

    compose.host.yaml 的 worker 是首轮遗漏、codex 才补上的——这类双文件
    漂移现在用测试钉住（读文件断言的先例见 test_shipped_ui_*）。"""
    # 零 runtime override（codex P2 on #384）：整列表替换基础挂载，必须
    # 保留其余全部挂载、只去掉 velites 一条——基础文件新增挂载时两处同步。
    zero = (ROOT / "deploy" / "compose.worker.zero-runtime.yaml").read_text(encoding="utf-8")
    assert "!override" in zero
    assert "/app/data/bin/velites" not in zero
    for mount in ("/etc/agent-legion/worker.yaml", "/root/.pi/agent", "/root/.velites"):
        assert mount in zero, f"zero-runtime override 丢了基础挂载 {mount}"

    for name in ("deploy/compose.worker.yaml", "deploy/compose.host.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "VELITES_BIN" in text, f"{name} 缺 velites 二进制挂载变量"
        assert "/app/data/bin/velites" in text, f"{name} 缺自带副本目录挂载"
        # 无冒号 ${VAR-default} 形式：显式置空 = 禁用守卫。
        assert "AGENT_WORKER_EXPECT_RUNTIMES: ${AGENT_WORKER_EXPECT_RUNTIMES-velites}" in text, (
            f"{name} 守卫注入缺失或插值形式错误"
        )
