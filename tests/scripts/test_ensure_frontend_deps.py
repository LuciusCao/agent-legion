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
- SIGKILL 强杀自愈：残留备份位（node_modules 已被清）先恢复再判定；
  备份与新目录并存（成功后清理前被杀）则弃备份、凭 stamp 跳过。
另钉三处调用点（native-prod-up / install-deps / dev_stack）统一委托本
脚本，旧的「node_modules 存在即跳过」判定不得残留——那是 #810 的直接
根因（升级 pull 进新 lockfile 后旧依赖构建新代码，tsc 报 TS2307）。
风格与 test_install_deps.py 的合成仓库 + PATH 桩手法一致。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
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


def _run(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # 真实 PATH 保留在后：摘要工具（sha256sum / shasum）不在桩内。
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["STUB_LOG"] = str(stub_log)
    env.update(extra_env or {})
    return subprocess.run(
        [_BASH, str(main / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _npm_ci_count(stub_log: Path) -> int:
    return sum(1 for line in stub_log.read_text().splitlines() if line == "npm ci")


def test_fresh_install_runs_npm_ci_and_writes_stamp(tmp_path: Path) -> None:
    """无 node_modules：npm ci 执行一次，成功后写入指纹 stamp，无备份残迹。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert _npm_ci_count(stub_log) == 1
    stamp = main / "frontend" / "node_modules" / ".deps-stamp"
    assert stamp.is_file()
    assert stamp.read_text() == f"{_fingerprint(main)}\n"
    assert not (main / "frontend" / ".node_modules.bak").exists()


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
    """备份位必须被 gitignore：强杀残留的备份目录不得污染 git status。"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "frontend/.node_modules.bak/" in gitignore


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
