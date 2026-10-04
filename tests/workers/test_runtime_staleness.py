"""#831/#835 velites 副本指纹对账（软告警）测试——解析到的家族副本 vs 仓库源码。

自 tests/workers/test_runtime_preflight.py 拆出（AGENTS.md 800 行阈值，
codex review on #835）：对账核心在 shared/velites_staleness.py，Worker 侧
组合面在 worker/runtime/staleness.py，经 runtime/setup.prepare_runtime_models
打启动告警。
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest

from shared import code_sandbox
from shared import velites_staleness as shared_staleness
from worker.runtime import setup as runtime_setup
from worker.runtime import staleness

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """自带二进制目录指向不存在的位置，避免开发机 data/bin 污染测试。"""
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", tmp_path / "no-bin")


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _all_missing(_binary: str) -> None:
    return None


# ---- #831/#835 指纹对账：解析到的 velites 家族副本 vs 仓库源码（软告警） ----
#
# 对账核心在 shared/velites_staleness.py（Host 侧钩子不 import worker 包，
# 两侧共用）；worker/runtime/staleness.py 组合 Worker 的两个消费面（agent
# runtime + code 沙箱）。#835 前对账只覆盖 runtime 面，恰好漏掉四轮 codex
# 评审的主战场——沙箱面（velites-sandbox 优先解析）。

# 有效指纹 fixture：40 位 hex tree hash（shared_staleness 的口径）。
_HEX_OLD = "a" * 40  # data/bin/PATH 副本的（旧）指纹
_HEX_NEW = "b" * 40  # 仓库当前（新）指纹


def _fake_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str = "",
    returncode: int = 0,
    raises: Exception | None = None,
) -> None:
    """把对账核心的 git tree-hash 探测替换为固定输出。

    只拦截 argv[0] == "git" 的调用，其余 subprocess 消费者（如
    probe_runtime_versions 的 --version 探测）转发真实实现——patch 打在
    全局 subprocess 模块上，无条件截获会静默伪造同进程内一切子进程行为
    （对抗式 review on #835：版本探测曾被本 stub 截获而不自知）。"""

    real_run = shared_staleness.subprocess.run

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

    monkeypatch.setattr(shared_staleness.subprocess, "run", _run)


def _bundle_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stamp: str) -> Path:
    """自带副本布局：bundled velites + 指定 stamp，PATH 全缺失。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{stamp}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)  # #496 真实读取点
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    return bundled_dir


@pytest.mark.no_db
def test_staleness_warning_fires_when_bundled_stamp_lags_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#831 核心场景：PATH 副本新、data/bin 副本旧——解析（自带副本优先）
    落在旧副本上，stamp 与仓库指纹不一致 → 返回告警文案（漂移可见）。"""
    bundled_dir = _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
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
def test_staleness_warning_covers_sandbox_resolution_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#835 主战场回归：runtime 面全新鲜、**沙箱面**解析到漂移副本。

    布局：bundled velites stamp 一致（agent runtime 面无漂移），但同目录
    有旧 velites-sandbox（候选序它优先）——沙箱面解析到它。#835 前对账只
    看 resolve_binary("velites")，此形态完全静默（四轮评审打的就是这里）；
    对账必须覆盖 Worker 的全部消费面。"""
    bundled_dir = tmp_path / "bundle"
    _write_executable(bundled_dir / "velites")
    (bundled_dir / "velites.src-stamp").write_text(f"{_HEX_NEW}\n", encoding="utf-8")
    _write_executable(bundled_dir / "velites-sandbox")
    (bundled_dir / "velites-sandbox.src-stamp").write_text(f"{_HEX_OLD}\n", encoding="utf-8")
    monkeypatch.setattr(code_sandbox, "BUNDLED_SANDBOX_DIR", bundled_dir)
    monkeypatch.setattr(shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    warning = staleness.velites_staleness_warning()

    assert warning is not None
    assert str(bundled_dir / "velites-sandbox") in warning
    assert "code 沙箱" in warning
    # runtime 面（velites 本体）无漂移——不产生该面的告警行/角色标签。
    assert "agent runtime" not in warning


@pytest.mark.no_db
def test_staleness_merges_roles_for_same_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一二进制被两个消费面解析（裸机无独立包装器：runtime 与沙箱都命中
    bundled velites）→ 一条告警合并全部角色，不做复读。"""

    bundled_dir = _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
    # 裸机无独立包装器形态：which 只对 velites 名返回假 PATH 位置，
    # velites-sandbox 无 PATH 副本——沙箱面兜底命中 bundled velites。
    monkeypatch.setattr(
        shutil, "which", lambda binary: f"/usr/local/bin/{binary}" if binary == "velites" else None
    )
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    warning = staleness.velites_staleness_warning()

    assert warning is not None
    assert warning.count(str(bundled_dir / "velites")) == 1
    assert "agent runtime" in warning
    assert "code 沙箱" in warning


@pytest.mark.no_db
def test_staleness_warning_silent_when_stamp_matches_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """指纹一致（prod-up 双通道刷新后的健康状态）→ 无告警。"""
    _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_NEW)
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
    _bundle_layout(monkeypatch, tmp_path, stamp="")
    (tmp_path / "bundle" / "velites.src-stamp").unlink(missing_ok=True)

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_silent_when_fingerprint_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """仓库指纹不可得（git 失败/无 velites 子树）→ 跳过，对账只在「有指纹
    可比」时进行（docker 形态 velites 版本独立管理，非漂移）。stamp 必须是
    有效 hex——否则在调 git 前就因垃圾内容静默，测不到本路径。"""
    _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
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
    bundled_dir = _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
    (bundled_dir / "velites.src-stamp").write_bytes(b"\xff\xfe broken \x80\n")
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    assert staleness.velites_staleness_warning() is None


@pytest.mark.no_db
def test_staleness_warning_never_raises_on_git_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git 探测的异常族（timeout / git 缺失 / 输出解码失败）逐一吞掉返回
    None——subprocess.run(text=True) 的解码异常与 stamp 读取同族（都曾被
    点状 except 白名单漏掉）。"""
    _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
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
    """总兜底不变量：对账核心抛出未枚举异常（未来代码增长引入的新读取点）
    时 velites_staleness_warning 仍返回 None——「漂移可见」绝不演变为
    「阻断启动」。"""
    monkeypatch.setattr(staleness, "reconcile_velites_copies", _raising_reconcile)
    assert staleness.velites_staleness_warning() is None


def _raising_reconcile(
    consumers: list[tuple[str, str | None]], *, repo_root: Path | None = None
) -> list[str]:
    raise RuntimeError("unforeseen failure mode")


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
    monkeypatch.setattr(staleness, "reconcile_velites_copies", _raising_reconcile)
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
    bundled_dir = _bundle_layout(monkeypatch, tmp_path, stamp="")

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
    _bundle_layout(monkeypatch, tmp_path, stamp=_HEX_OLD)
    _fake_git_stub(monkeypatch, f"{_HEX_NEW}\n")

    for layout in ("empty", "git-only", "velites-without-git"):
        root = tmp_path / f"root-{layout}"
        root.mkdir()
        if layout == "git-only":
            (root / ".git").mkdir()
        if layout == "velites-without-git":
            (root / "velites").mkdir()
        monkeypatch.setattr(shared_staleness, "_REPO_ROOT", root)
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
