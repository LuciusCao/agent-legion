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
from worker import executor as agent_worker
from worker.binary_resolution import resolve_binary
from worker.runtime import setup as runtime_setup
from worker.runtime import staleness
from worker.runtime.preflight import parse_expect_runtimes, preflight_error

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。

    目录常量定义在 shared/code_sandbox.py（BUNDLED_SANDBOX_DIR），
    worker/binary_resolution.py re-export 为 BUNDLED_BINARY_DIR——模块属性
    各自独立，两侧都要 patch，runtime 解析与沙箱解析（#383）才同时隔离。"""
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bin")  # #496 真实读取点
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
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
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
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
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
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
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
    assert str(code_sandbox.BUNDLED_SANDBOX_DIR) in error  # #496 与解析同源
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
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
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


# ---- #831 指纹对账：解析到的 velites 副本 vs 仓库源码（软告警） ----

# 有效指纹 fixture：40 位 hex tree hash（staleness 的 _FINGERPRINT_RE 口径）。
_HEX_OLD = "a" * 40  # data/bin/PATH 副本的（旧）指纹
_HEX_NEW = "b" * 40  # 仓库当前（新）指纹


def _fake_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str = "",
    returncode: int = 0,
    raises: Exception | None = None,
) -> None:
    """把 staleness 的 git tree-hash 探测替换为固定输出。

    只拦截 argv[0] == "git" 的调用，其余 subprocess 消费者（如
    probe_runtime_versions 的 --version 探测）转发真实实现——patch 打在
    全局 subprocess 模块上，无条件截获会静默伪造同进程内一切子进程行为
    （对抗式 review on #835：版本探测曾被本 stub 截获而不自知）。"""

    real_run = staleness.subprocess.run

    class _Result:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def _run(*args: object, **kwargs: object) -> object:
        argv = args[0] if args else kwargs.get("args")
        if isinstance(argv, list) and argv and argv[0] == "git":
            if raises is not None:
                raise raises
            return _Result()
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(staleness.subprocess, "run", _run)


@pytest.mark.no_db
def test_staleness_warning_fires_when_bundled_stamp_lags_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#831 核心场景：PATH 副本新、data/bin 副本旧——解析（自带副本优先）
    落在旧副本上，stamp 与仓库指纹不一致 → 返回告警文案（漂移可见）。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    warning = staleness.velites_staleness_warning()

    assert warning is not None
    assert str(bundled_dir / "velites") in warning
    assert _HEX_OLD[:12] in warning
    assert _HEX_NEW[:12] in warning
    assert "ensure-velites.sh" in warning
    # 方向中性（PATH 副本跨 worktree 共享，副本可能新于本仓库版本线）。
    assert "落后" in warning
    assert "领先" in warning


@pytest.mark.no_db
def test_staleness_warning_silent_when_stamp_matches_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """指纹一致（prod-up 双通道刷新后的健康状态）→ 无告警。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_NEW}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_reconciles_resolved_path_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """对账对象是**实际解析到**的二进制：无自带副本时对 PATH 副本的 stamp
    对账（ensure-velites.sh 默认模式安置 PATH 副本时同样留 stamp）。"""
    path_velites = tmp_path / "path-velites"
    _write_executable(path_velites)
    (tmp_path / "path-velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _binary: str(path_velites))
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    warning = staleness.velites_staleness_warning()

    assert warning is not None
    assert str(path_velites) in warning


@pytest.mark.no_db
def test_staleness_warning_silent_without_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """无 stamp（GitHub Release 产物/手工安置）→ 无从对账，不告警。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_silent_when_fingerprint_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """仓库指纹不可得（git 失败/无 velites 子树）→ 跳过，对账只在「有指纹
    可比」时进行（docker 形态 velites 版本独立管理，非漂移）。stamp 必须是
    有效 hex——否则在调 git 前就因垃圾内容静默，测不到本路径。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, "", returncode=128)

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_silent_when_velites_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """velites 不可解析（零 runtime 形态）→ 无对账对象，不告警。"""
    monkeypatch.setattr(shutil, "which", _all_missing)

    assert staleness.velites_staleness_warning() is None


# ---- 软告警不变量：任何失败形态都不得抛出/阻断启动（codex P2 on #835） ----


@pytest.mark.no_db
def test_staleness_warning_never_raises_on_corrupt_stamp_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """codex P2 本体：stamp 含非 UTF-8 字节（手工部署/写入中断/损坏）时
    read_text 抛 UnicodeDecodeError——对账必须吞掉返回 None，不得穿透到
    prepare_runtime_models 使 Worker crash-loop（supervisor 对退出码 1 走
    自动重启并每轮重置 claim_enabled）。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_bytes(b"\xff\xfe broken \x80\n")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_never_raises_on_git_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git 探测的异常族（timeout / git 缺失 / 输出解码失败）逐一吞掉返回
    None——subprocess.run(text=True) 的解码异常与 stamp 读取同族（都曾被
    点状 except 白名单漏掉）。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    import subprocess as _sp

    for failure in (
        _sp.TimeoutExpired(cmd=["git"], timeout=10),
        FileNotFoundError("git"),
    ):
        _fake_git_stub(monkeypatch, raises=failure)
        assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_backstop_swallows_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """总兜底不变量：_reconcile 抛出未枚举异常（未来代码增长引入的新读取
    点）时 velites_staleness_warning 仍返回 None——「漂移可见」绝不演变为
    「阻断启动」。"""

    def _boom() -> str | None:
        raise RuntimeError("unforeseen failure mode")

    monkeypatch.setattr(staleness, "_reconcile", _boom)
    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_prepare_runtime_models_survives_staleness_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """接线级不变量：对账内部炸掉时 prepare_runtime_models 照常完成（返回
    None、注入发现结果），只是没有告警行——启动路径零依赖软告警。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    def _boom() -> str | None:
        raise RuntimeError("unforeseen failure mode")

    monkeypatch.setattr(staleness, "_reconcile", _boom)
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: ([{"runtime": "velites", "provider": "sqai", "model": "kimi"}], {}),
    )

    config = {"disabled_runtimes": [], "models": []}
    assert runtime_setup.prepare_runtime_models(config) is None
    assert config["models"] == [{"runtime": "velites", "provider": "sqai", "model": "kimi"}]
    assert "源码指纹" not in capsys.readouterr().out


# ---- 对账输入的形态校验（假告警/挂起防线，对抗式 review 簇 A） ----


@pytest.mark.no_db
def test_staleness_silent_on_garbage_stamp_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """stamp 内容非纯 hex（跨版本格式变化/手工随意写入）→ 按不可对账跳过：
    垃圾内容与仓库指纹必然不等，放行会变成每次启动的假告警 + 多行内容
    注入启动日志。git 输出同校验（rc=0 但非纯 hash 的包装器输出）。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")

    for content in ("not-a-hash\n", "HEAD detached at abc123\nextra line\n", "short\n"):
        (bundled_dir / "velites.src-stamp").write_text(content, encoding="utf-8")
        _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")
        assert staleness.velites_staleness_warning() is None, content

    # 非法长度（41/63 位）：git 对象 ID 只有 40（SHA-1）与 64（SHA-256）
    # 两种，中间长度是截断/损坏的征兆——按不可对账跳过，不做必然不等的
    # 假告警（codex P3 on #835）。
    for bad_len in (39, 41, 63, 65):
        (bundled_dir / "velites.src-stamp").write_text(f"{'a' * bad_len}\n", encoding="utf-8")
        _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")
        assert staleness.velites_staleness_warning() is None, bad_len
    # 合法长度（40/64）照常对账：漂移告警在长度合法时仍触发。
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")
    assert staleness.velites_staleness_warning() is not None

    # git 侧：rc=0 但输出非纯 hex（PATH 上的 git 包装器多打了一行）。
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    _fake_git_stub(monkeypatch, "hint: using detached HEAD\nabc123\n")
    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_silent_on_odd_stamp_file_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """stamp 为 FIFO（open 挂死——比崩溃更难诊断的启动挂起）、目录、超限
    巨文件（全量读入的 OOM 防线）→ 一律按不可对账跳过。"""
    import os

    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    fifo = bundled_dir / "velites.src-stamp"
    os.mkfifo(fifo)
    assert staleness.velites_staleness_warning() is None  # is_file() 排除 FIFO
    fifo.unlink()

    (bundled_dir / "velites.src-stamp").mkdir()
    assert staleness.velites_staleness_warning() is None
    (bundled_dir / "velites.src-stamp").rmdir()

    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD * 4}\n", encoding="utf-8")
    assert staleness.velites_staleness_warning() is None  # 超 128 字节上限


@pytest.mark.no_db
def test_staleness_skips_git_when_repo_root_lacks_git_or_velites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """前置形态检查：仓库根无 .git（worker/+shared/ 被拷到非 repo 目录）或
    无 velites/ 子树时不调 git——git -C 会向上发现无关的祖先仓库，若它恰有
    velites/ 子树会拿到它的 tree hash 制造假告警。stub 返回有效 hash 仍得
    None，证明前置检查先于 git 短路。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    for layout in ("empty", "git-only", "velites-without-git"):
        root = tmp_path / f"root-{layout}"
        root.mkdir()
        if layout == "git-only":
            (root / ".git").mkdir()
        if layout == "velites-without-git":
            (root / "velites").mkdir()
        monkeypatch.setattr(staleness, "_REPO_ROOT", root)
        assert staleness.velites_staleness_warning() is None, layout


@pytest.mark.no_db
def test_prepare_runtime_models_prints_staleness_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """启动预检接线：prepare_runtime_models 把 #831 对账告警打进启动日志
    （漂移可见但不 fail-closed——velites 版本线独立，允许刻意落后）。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")
    monkeypatch.setattr(
        runtime_setup,
        "discover_effective_models",
        lambda _config: ([], {}),
    )

    config = {"disabled_runtimes": [], "models": []}
    assert runtime_setup.prepare_runtime_models(config) is None
    out = capsys.readouterr().out
    assert "源码指纹" in out
    assert "ensure-velites.sh" in out
    assert config.get("models") == []


@pytest.mark.no_db
def test_preflight_reexports_staleness_warning() -> None:
    """preflight.velites_staleness_warning 是 staleness 模块的名字
    re-export——setup.py 等调用方依赖该导入路径，别名漂移会在启动接线处
    静默断链。"""
    import worker.runtime.preflight as pf
    import worker.runtime.staleness as st

    assert pf.velites_staleness_warning is st.velites_staleness_warning


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
