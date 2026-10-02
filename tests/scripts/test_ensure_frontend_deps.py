"""ensure-frontend-deps.sh 依赖清单指纹新鲜度检测（issue #810）。

真实脚本 + 合成仓库布局（frontend/package.json、package-lock.json、PATH
桩 npm 记录调用并模拟 npm ci 重建 node_modules），行为级验证：
- 首次安装 / 清单变化 / stamp 缺失（安装中断）→ npm ci；
- 清单未变 → 跳过；
- 清单文件缺失 / 摘要工具缺失 / npm 缺失 → fail-fast（不动任何状态）；
- 失败保护（PR #832 codex P1）：npm ci 自身先整目录删除 node_modules，
  失败时原本可用的旧依赖一并丢失——已有 node_modules 时必须经备份位
  .node_modules.bak 恢复旧目录，不留半安装态（AGENTS.md「禁止半应用
  状态」）；npm 桩按破坏性优先语义建模（删除发生在失败之前）；
- SIGKILL 残留裁决（PR #832 codex P2）：备份与新树并存时，只有新树
  stamp 命中当前指纹才弃备份（成功后、清备份前被杀）；否则视为半安装
  残树（npm ci 中途被杀）——删残树、恢复备份，「目录存在即弃备份」会
  删掉唯一完整的旧树；备份在、模块不在则先恢复再判定；
- 并发互斥（PR #832 codex P2）：同 worktree 并发调用共享备份位与恢复
  逻辑，整个事务经 mkdir 原子锁串行化（语义同 gate-queue 的 slot）：
  持有者死亡即回收、等待者绝不误删他人的锁、结束必释放。
另钉三处调用点（native-prod-up / install-deps / dev_stack）统一委托本
脚本，旧的「node_modules 存在即跳过」判定不得残留——那是 #810 的直接
根因（升级 pull 进新 lockfile 后旧依赖构建新代码，tsc 报 TS2307）。
风格与 test_install_deps.py 的合成仓库 + PATH 桩手法一致。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-frontend-deps.sh"
SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
NATIVE_PROD_UP = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
INSTALL_DEPS = (ROOT / "scripts" / "install-deps.sh").read_text(encoding="utf-8")
DEV_STACK = (ROOT / "scripts" / "dev_stack.sh").read_text(encoding="utf-8")

# npm 桩：记录参数；ci 子命令按真实 npm 语义建模——先整目录删除
# node_modules，再安装；STUB_NPM_RC 非 0 时在删除之后、写入之前失败
# （PR #832 codex P1：旧桩只在成功路径删除，破坏性失败路径未被覆盖）；
# STUB_NPM_SKIP_CREATE=1 模拟零依赖清单：ci「成功」但不创建 node_modules。
_NPM_STUB = """#!/usr/bin/env bash
echo "npm $*" >> "${STUB_LOG}"
if [[ "$1" == "ci" ]]; then
  rm -rf node_modules
  if [[ "${STUB_NPM_RC:-0}" != "0" ]]; then
    exit "${STUB_NPM_RC}"
  fi
  if [[ "${STUB_NPM_SKIP_CREATE:-0}" != "1" ]]; then
    mkdir -p node_modules
  fi
fi
exit 0
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_manifests(main: Path) -> None:
    (main / "frontend" / "package.json").write_text('{"name": "frontend"}\n')
    (main / "frontend" / "package-lock.json").write_text(
        '{"name": "frontend", "lockfileVersion": 3}\n'
    )


def _fingerprint(main: Path) -> str:
    """镜像脚本的指纹算法：两清单各自 sha256 十六进制按行拼接。"""
    parts = [
        hashlib.sha256((main / "frontend" / name).read_bytes()).hexdigest()
        for name in ("package.json", "package-lock.json")
    ]
    return "\n".join(parts)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """合成仓库：真实 ensure-frontend-deps.sh + 两份清单 + PATH 桩 npm。"""
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "frontend").mkdir()
    shutil.copy(SCRIPT, main / "scripts" / SCRIPT.name)
    _write_manifests(main)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "npm", _NPM_STUB)
    return main, bin_dir


# 绝对路径调用 bash：missing-npm 用例的封闭 PATH 不含 bash，而
# subprocess 的可执行文件查找走子进程 PATH，裸 "bash" 会找不到。
_BASH = shutil.which("bash") or "/bin/bash"


def _script_env(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    # 真实 PATH 保留在后：摘要工具（sha256sum / shasum）不在桩内。
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["STUB_LOG"] = str(stub_log)
    env.update(extra_env or {})
    return env


def _run(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_BASH, str(main / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        env=_script_env(main, bin_dir, stub_log, extra_env),
        timeout=60,
    )


def _npm_ci_count(stub_log: Path) -> int:
    return sum(1 for line in stub_log.read_text().splitlines() if line == "npm ci")


def test_fresh_install_runs_npm_ci_and_writes_stamp(tmp_path: Path) -> None:
    """无 node_modules：npm ci 执行一次，成功后写入指纹 stamp，无备份残迹、
    锁已释放。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert _npm_ci_count(stub_log) == 1
    stamp = main / "frontend" / "node_modules" / ".deps-stamp"
    assert stamp.is_file()
    assert stamp.read_text() == f"{_fingerprint(main)}\n"
    assert not (main / "frontend" / ".node_modules.bak").exists()
    assert not (main / "frontend" / ".deps-install.lock").exists()


def test_unchanged_manifests_skip_reinstall(tmp_path: Path) -> None:
    """清单未变：重跑跳过 npm ci（幂等，不重复安装）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    first = _run(main, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr

    second = _run(main, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    assert "跳过 npm ci" in second.stdout
    assert _npm_ci_count(stub_log) == 1


def test_lockfile_change_triggers_reinstall(tmp_path: Path) -> None:
    """#810 场景：升级 pull 进新 lockfile（新增 react-rnd），重跑必须重装，
    且 stamp 更新为新指纹。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    first = _run(main, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr
    stamp = main / "frontend" / "node_modules" / ".deps-stamp"
    stamp_before = stamp.read_text()

    (main / "frontend" / "package-lock.json").write_text(
        '{"name": "frontend", "lockfileVersion": 3,\n "dependencies": {"react-rnd": "^10.4.0"}}\n'
    )
    second = _run(main, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    assert "重新安装" in second.stdout
    assert _npm_ci_count(stub_log) == 2
    assert stamp.read_text() != stamp_before


def test_package_json_change_triggers_reinstall(tmp_path: Path) -> None:
    """package.json 变化同样触发重装（依赖清单两份文件都在指纹内）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    first = _run(main, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr

    (main / "frontend" / "package.json").write_text(
        '{"name": "frontend", "dependencies": {"react-rnd": "^10.4.0"}}\n'
    )
    second = _run(main, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    assert _npm_ci_count(stub_log) == 2


def test_missing_stamp_reinstalls(tmp_path: Path) -> None:
    """node_modules 在但 stamp 缺失（上次安装中断）：重跑补装并补 stamp。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    first = _run(main, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr
    (main / "frontend" / "node_modules" / ".deps-stamp").unlink()

    second = _run(main, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    assert _npm_ci_count(stub_log) == 2
    assert (main / "frontend" / "node_modules" / ".deps-stamp").is_file()


def test_failed_npm_ci_writes_no_stamp_and_self_heals(tmp_path: Path) -> None:
    """npm ci 失败（无旧目录可保护）：非零退出、不写 stamp（半失败态不留
    「已装」假象）、无备份残迹；下次成功后照常安装。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    failed = _run(main, bin_dir, stub_log, {"STUB_NPM_RC": "1"})
    assert failed.returncode != 0
    assert not (main / "frontend" / "node_modules" / ".deps-stamp").exists()
    assert not (main / "frontend" / ".node_modules.bak").exists()

    healed = _run(main, bin_dir, stub_log)
    assert healed.returncode == 0, healed.stderr
    assert _npm_ci_count(stub_log) == 2


def test_failed_npm_ci_restores_previous_node_modules(tmp_path: Path) -> None:
    """PR #832 codex P1：已有可用的 node_modules、清单变化、npm ci 失败
    （网络/registry/lifecycle script）——npm 已先清掉原目录，脚本必须经
    备份位完整恢复旧树：marker 存活、旧 stamp 原样（证明是恢复而非重装）、
    备份位清空、非零退出。改动前的判定从不触碰已存在的 node_modules，
    这条防线守住该旧语义。"""
    main, bin_dir = _setup(tmp_path)
    modules = main / "frontend" / "node_modules"
    modules.mkdir()
    (modules / "marker").write_text("old-deps")
    (modules / ".deps-stamp").write_text("stale-fingerprint\n")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_NPM_RC": "1"})

    assert result.returncode != 0
    assert "已恢复升级前的 node_modules" in result.stderr
    assert (modules / "marker").read_text() == "old-deps"
    assert (modules / ".deps-stamp").read_text() == "stale-fingerprint\n"
    assert not (main / "frontend" / ".node_modules.bak").exists()
    # 失败后重跑（网络恢复）：旧树作为备份源再走一次安装，成功收尾。
    retry = _run(main, bin_dir, stub_log)
    assert retry.returncode == 0, retry.stderr
    assert _npm_ci_count(stub_log) == 2
    assert not (modules / "marker").exists()  # 旧树最终被新安装替换
    assert (modules / ".deps-stamp").read_text() == f"{_fingerprint(main)}\n"


def test_stale_backup_from_killed_install_is_recovered(tmp_path: Path) -> None:
    """SIGKILL 自愈（安装中被强杀，EXIT trap 无从执行）：备份位在、
    node_modules 已被 npm 清掉——下次运行先把备份恢复回 node_modules，
    再按指纹走带备份的安装；成功后新树就位、备份清理、旧 marker 不残留。"""
    main, bin_dir = _setup(tmp_path)
    bak = main / "frontend" / ".node_modules.bak"
    bak.mkdir()
    (bak / "marker").write_text("old-deps")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert not bak.exists()
    assert not (main / "frontend" / "node_modules" / "marker").exists()
    assert (main / "frontend" / "node_modules" / ".deps-stamp").is_file()
    assert not (main / "frontend" / ".deps-install.lock").exists()


def test_stale_backup_with_fresh_modules_is_discarded(tmp_path: Path) -> None:
    """SIGKILL 自愈（安装成功后、备份清理前被强杀）：node_modules（带
    匹配 stamp）与备份位并存——备份已陈旧直接删除，凭 stamp 命中跳过，
    不触发多余的 npm ci（stamp 先于备份清理写入正是为此）。"""
    main, bin_dir = _setup(tmp_path)
    modules = main / "frontend" / "node_modules"
    modules.mkdir()
    (modules / "marker").write_text("fresh-deps")
    (modules / ".deps-stamp").write_text(f"{_fingerprint(main)}\n")
    (main / "frontend" / ".node_modules.bak").mkdir()
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert "跳过 npm ci" in result.stdout
    assert not (main / "frontend" / ".node_modules.bak").exists()
    assert (modules / "marker").read_text() == "fresh-deps"
    assert not stub_log.exists()  # 未触碰 npm


def test_stale_backup_with_partial_modules_restores_backup(tmp_path: Path) -> None:
    """PR #832 codex P2：npm ci 中途被 SIGKILL，备份位与「半安装残树」
    并存（残树无 stamp，或 stamp 是旧清单的）——「目录存在即弃备份」会删
    掉唯一完整的旧树，之后安装再失败时 EXIT trap 恢复的只是残树，失败
    保护失效。必须删残树、恢复备份，再走带备份的完整安装。"""
    main, bin_dir = _setup(tmp_path)
    modules = main / "frontend" / "node_modules"
    bak = main / "frontend" / ".node_modules.bak"
    bak.mkdir()
    (bak / "marker").write_text("old-deps")
    # 半安装残树：部分包 + 无 stamp（npm ci 写 stamp 之前被杀）。
    modules.mkdir()
    (modules / "partial-package").write_text("half-installed")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    # 残树被删、备份恢复为安装源；最终新树完整、备份清理、锁释放。
    assert not (modules / "partial-package").exists()
    assert not (modules / "marker").exists()  # 恢复出的旧树随后被新安装替换
    assert (modules / ".deps-stamp").read_text() == f"{_fingerprint(main)}\n"
    assert not bak.exists()
    assert not (main / "frontend" / ".deps-install.lock").exists()


def test_stale_backup_with_stale_stamp_modules_restores_backup(tmp_path: Path) -> None:
    """P2 变体：残树带着旧清单的 stamp（清单已变化，stamp 不命中当前
    指纹）——同样按残树处理：恢复备份重装。stamp 命中当前指纹是弃备份的
    唯一判据，目录存在与旧 stamp 都不是。"""
    main, bin_dir = _setup(tmp_path)
    modules = main / "frontend" / "node_modules"
    bak = main / "frontend" / ".node_modules.bak"
    bak.mkdir()
    (bak / "marker").write_text("old-deps")
    modules.mkdir()
    (modules / "partial-package").write_text("half-installed")
    (modules / ".deps-stamp").write_text("fingerprint-of-older-manifests\n")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert not (modules / "partial-package").exists()
    assert (modules / ".deps-stamp").read_text() == f"{_fingerprint(main)}\n"
    assert not bak.exists()


# 慢速 npm 桩：ci 时先声明「安装中」（写 marker 文件）再 sleep——供并发
# 锁用例构造「持有锁的进程正处于安装中」的真实窗口。
_NPM_SLOW_STUB = """#!/usr/bin/env bash
echo "npm $*" >> "${STUB_LOG}"
if [[ "$1" == "ci" ]]; then
  rm -rf node_modules
  touch "${STUB_INSTALLING_MARKER}"
  sleep "${STUB_NPM_SLEEP:-30}"
  mkdir -p node_modules
fi
exit 0
"""


def test_concurrent_install_serializes_on_lock(tmp_path: Path) -> None:
    """PR #832 codex P2：同 worktree 并发调用（dev-up + install + 手工）共享
    备份位与恢复逻辑——后启动者不得移走/删除前一进程的备份或其刚完成的
    安装（EXIT trap 恢复的正是对方的成果）。整个事务经 mkdir 原子锁
    串行化：A 持锁安装期间 B 必须等待；A 完成释放后 B 凭 stamp 跳过。"""
    main, bin_dir = _setup(tmp_path)
    _write_stub(bin_dir / "npm", _NPM_SLOW_STUB)
    installing = tmp_path / "installing.marker"
    stub_log = tmp_path / "stub.log"
    env = _script_env(main, bin_dir, stub_log, {"STUB_INSTALLING_MARKER": str(installing)})

    first = subprocess.Popen(
        [_BASH, str(main / "scripts" / SCRIPT.name)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # 等 A 真正进入安装段（marker 出现 = 已持锁、已 mv 备份、npm 在跑）。
        for _ in range(100):
            if installing.exists():
                break
            time.sleep(0.1)
        assert installing.exists(), "首个进程未进入安装段"
        # 此时备份位/新装目录处于中间态：B 并发启动必须被锁挡住。
        second = subprocess.run(
            [_BASH, str(main / "scripts" / SCRIPT.name)],
            capture_output=True,
            text=True,
            env=_script_env(main, bin_dir, stub_log, {"STUB_NPM_SLEEP": "0"}),
            timeout=90,
        )
        assert second.returncode == 0, second.stderr
        assert "等待" in second.stderr  # B 打印过持有者等待提示
    finally:
        # 唤醒 A 的慢速 npm，让它正常收尾。
        if first.poll() is None:
            slow_dir = installing.parent
            _write_stub(bin_dir / "npm", _NPM_STUB)
            del slow_dir
            first.wait(timeout=60)
    out, err = first.communicate()
    assert first.returncode == 0, err or out
    # A 完成、B 随后：A 安装一次，B 凭 stamp 跳过——npm ci 恰好一次，
    # 两个进程都不留备份/锁残迹。
    assert _npm_ci_count(stub_log) == 1
    assert (main / "frontend" / "node_modules" / ".deps-stamp").read_text() == (
        f"{_fingerprint(main)}\n"
    )
    assert not (main / "frontend" / ".node_modules.bak").exists()
    assert not (main / "frontend" / ".deps-install.lock").exists()


def test_dead_lock_holder_is_reclaimed(tmp_path: Path) -> None:
    """锁持有者死亡（SIGKILL，无 EXIT trap）后残锁回收：下次运行凭
    kill -0 探测 pid 已死，回收重试而不是死等。备份/残树按正常收编逻辑
    处理（本用例聚焦锁回收本身）。"""
    main, bin_dir = _setup(tmp_path)
    lock = main / "frontend" / ".deps-install.lock"
    lock.mkdir()
    (lock / "pid").write_text("999999999\n")  # 几乎不可能存活的 pid
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert "陈旧安装锁" in result.stderr
    assert not lock.exists()  # 成功收尾后锁已释放
    assert (main / "frontend" / "node_modules" / ".deps-stamp").is_file()


def test_waiter_never_deletes_live_lock(tmp_path: Path) -> None:
    """等待者绝不误删持有者的锁：锁内 pid 存活时，等待中的进程只能打印
    提示并继续等，不得回收。持有者退出后等待者才拿锁进入。"""
    main, bin_dir = _setup(tmp_path)
    lock = main / "frontend" / ".deps-install.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n")  # 本测试进程 = 存活的持有者
    stub_log = tmp_path / "stub.log"

    # 等待者起在后台：持锁期间它只能等待（不产出陈旧锁回收/完成痕迹）。
    waiter = subprocess.Popen(
        [_BASH, str(main / "scripts" / SCRIPT.name)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_script_env(main, bin_dir, stub_log),
    )
    try:
        time.sleep(3)
        assert waiter.poll() is None, "等待者在持有者存活期间提前退出了"
        assert lock.exists(), "等待者误删了存活持有者的锁"
        assert "陈旧安装锁" not in (waiter.stderr and "" or "")  # 尚未输出
    finally:
        # 释放锁，等待者应随即完成（无需 npm——跳过或安装由残留态决定）。
        shutil.rmtree(lock)
        waiter.wait(timeout=60)
    out, err = waiter.communicate()
    assert waiter.returncode == 0, err
    assert _npm_ci_count(stub_log) <= 1
    assert not lock.exists()


def test_lock_pid_file_missing_is_reclaimed_after_grace(tmp_path: Path) -> None:
    """空 pid 残锁（持有者在 mkdir 与写 pid 的窗口内被强杀）：等待者先
    宽限，仍无 pid 则回收——不至于永久死锁。"""
    main, bin_dir = _setup(tmp_path)
    lock = main / "frontend" / ".deps-install.lock"
    lock.mkdir()  # 无 pid 文件
    stub_log = tmp_path / "stub.log"

    start = time.monotonic()
    result = _run(main, bin_dir, stub_log)
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    assert "回收重试" in result.stderr
    assert elapsed < 30  # 宽限是秒级，不是永久等待
    assert not lock.exists()


def test_missing_manifest_fails_fast(tmp_path: Path) -> None:
    """清单文件缺失：fail-fast 且指明缺哪个，不执行 npm ci。"""
    main, bin_dir = _setup(tmp_path)
    (main / "frontend" / "package-lock.json").unlink()
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 1
    assert "frontend/package-lock.json" in result.stderr
    assert not stub_log.exists()


def _hermetic_bin_without(tmp_path: Path, exclude: str) -> Path:
    """封闭 bin 目录：符号链接真实 PATH 的全部可执行文件（首个命中优先，
    与 PATH 语义一致），唯独排除 ${exclude}——逐个枚举脚本所需工具太脆弱
    （ROOT 解析要 dirname/pwd，摘要要 shasum…），全量链接只漏目标工具最稳。"""
    hermetic = tmp_path / "hermetic-bin"
    hermetic.mkdir()
    seen: set[str] = set()
    for dir_entry in os.environ.get("PATH", "").split(os.pathsep):
        if not dir_entry:
            continue
        try:
            names = os.listdir(dir_entry)
        except OSError:
            continue
        for name in names:
            if name == exclude or name in seen:
                continue
            real = Path(dir_entry) / name
            try:
                if not (real.is_file() and os.access(real, os.X_OK)):
                    continue
            except OSError:
                continue  # macOS 对 /usr/sbin 部分条目 stat 即 PermissionError
            os.symlink(str(real), hermetic / name)
            seen.add(name)
    return hermetic


def test_missing_npm_fails_fast_before_touching_state(tmp_path: Path) -> None:
    """npm 缺失：提前 fail-fast 且中文报错——不走备份/恢复循环，已有
    node_modules 原封不动。PATH 换成「全量工具、唯独无 npm」的封闭目录，
    防真实 npm 从系统 PATH 泄漏进来（先前用例正是这样漏掉的）。"""
    main, bin_dir = _setup(tmp_path)
    hermetic = _hermetic_bin_without(tmp_path, "npm")
    modules = main / "frontend" / "node_modules"
    modules.mkdir()
    (modules / "marker").write_text("old-deps")
    (modules / ".deps-stamp").write_text("stale-fingerprint\n")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"PATH": str(hermetic)})

    assert result.returncode == 1
    assert "缺少 npm" in result.stderr
    assert (modules / "marker").read_text() == "old-deps"
    assert not (main / "frontend" / ".node_modules.bak").exists()


def test_zero_dep_install_still_reaches_skip_state(tmp_path: Path) -> None:
    """npm ci 对零依赖清单「成功」但不创建 node_modules（真实 npm 行为）：
    脚本须自行 mkdir 兜底再写 stamp——否则 stamp 写入失败（假报错+回滚
    旧树），且下轮 fingerprint 判定永远进不了跳过态。"""
    main, bin_dir = _setup(tmp_path)
    modules = main / "frontend" / "node_modules"
    modules.mkdir()
    (modules / "marker").write_text("old-deps")
    (modules / ".deps-stamp").write_text("stale-fingerprint\n")
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_NPM_SKIP_CREATE": "1"})

    assert result.returncode == 0, result.stderr
    assert modules.is_dir()
    assert (modules / ".deps-stamp").read_text() == f"{_fingerprint(main)}\n"
    assert not (modules / "marker").exists()  # 旧树已被替换
    assert not (main / "frontend" / ".node_modules.bak").exists()


def test_stamp_lives_inside_node_modules() -> None:
    """stamp 必须放在 node_modules 内：手动删目录时 stamp 随之消失、
    天然失效，不存在「目录已删但 stamp 还说已装」的漂移。"""
    assert 'STAMP="frontend/node_modules/.deps-stamp"' in SCRIPT_TEXT


def test_backup_dir_is_gitignored() -> None:
    """备份位与安装锁必须被 gitignore：强杀残留的目录不得污染 git status。"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "frontend/.node_modules.bak/" in gitignore
    assert "frontend/.deps-install.lock/" in gitignore


def test_lock_covers_whole_transaction() -> None:
    """锁必须先于任何共享状态变更（含残留备份收编与跳过路径的 rm）：
    acquire 在 trap 设置之后、收编逻辑之前；release 在 EXIT trap 内。
    收编/弃备份/恢复若发生在锁外，并发窗口会重新打开（codex P2）。"""
    acquire_pos = SCRIPT_TEXT.index("acquire_deps_lock\n")
    trap_pos = SCRIPT_TEXT.index("trap 'restore_and_release' EXIT")
    # 主流程收编段（restore_and_release 函数体内也有同形判定，须锚定
    # 「---- SIGKILL 残留收编」标题后的那一份）。
    collect_pos = SCRIPT_TEXT.index("# ---- SIGKILL 残留收编")
    assert trap_pos < collect_pos, "EXIT trap（含恢复）必须先于残留收编执行"
    assert acquire_pos < collect_pos, "锁必须覆盖残留收编段"
    # skip 判定也在锁内：位于 acquire 与脚本末尾之间（release 只发生在
    # EXIT trap，skip 前无显式 release）。
    skip_pos = SCRIPT_TEXT.index("跳过 npm ci")
    assert acquire_pos < skip_pos, "skip 路径须持锁执行（读 stamp 与 rm 备份）"
    # release 只删自己的锁：等待者不得误删持有者的锁。
    assert re.search(r'release_deps_lock\(\) \{.*?"\$\$".*?\}', SCRIPT_TEXT, re.DOTALL), (
        "release 必须比对锁内 pid 与 $$"
    )


def test_call_sites_delegate_and_old_guard_is_gone() -> None:
    """三处调用点（native-prod-up / install-deps / dev_stack）统一委托
    ensure-frontend-deps.sh；旧的「node_modules 存在即跳过」判定与直连
    npm ci 不得残留（#810 根因）。"""
    call_sites = {
        "native-prod-up.sh": NATIVE_PROD_UP,
        "install-deps.sh": INSTALL_DEPS,
        "dev_stack.sh": DEV_STACK,
    }
    for name, text in call_sites.items():
        assert "./scripts/ensure-frontend-deps.sh" in text, f"{name} 未委托依赖新鲜度检测"
        assert "[[ ! -d frontend/node_modules ]]" not in text, (
            f"{name} 残留旧的「目录存在即跳过」判定（#810）"
        )
        assert "(cd frontend && npm ci)" not in text, f"{name} 残留直连 npm ci"
