"""ensure-frontend-deps.sh 依赖清单指纹新鲜度检测（issue #810）。

真实脚本 + 合成仓库布局（frontend/package.json、package-lock.json、PATH
桩 npm 记录调用并模拟 npm ci 重建 node_modules），行为级验证：
- 首次安装 / 清单变化 / stamp 缺失（安装中断）→ npm ci；
- 清单未变 → 跳过；
- npm ci 失败不写 stamp，下次重跑自愈；
- 清单文件缺失 → fail-fast。
另钉三处调用点（native-prod-up / install-deps / dev_stack）统一委托本
脚本，旧的「node_modules 存在即跳过」判定不得残留——那是 #810 的直接
根因（升级 pull 进新 lockfile 后旧依赖构建新代码，tsc 报 TS2307）。
风格与 test_install_deps.py 的合成仓库 + PATH 桩手法一致。
"""

from __future__ import annotations

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

# npm 桩：记录参数；ci 子命令模拟 npm ci 的副作用（整目录重建 node_modules），
# STUB_NPM_RC 非 0 时在重建前失败（模拟网络/清单不一致等安装失败）。
_NPM_STUB = """#!/usr/bin/env bash
echo "npm $*" >> "${STUB_LOG}"
if [[ "$1" == "ci" && "${STUB_NPM_RC:-0}" != "0" ]]; then
  exit "${STUB_NPM_RC}"
fi
if [[ "$1" == "ci" ]]; then
  rm -rf node_modules
  mkdir -p node_modules
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
        ["bash", str(main / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _npm_ci_count(stub_log: Path) -> int:
    return sum(1 for line in stub_log.read_text().splitlines() if line == "npm ci")


def test_fresh_install_runs_npm_ci_and_writes_stamp(tmp_path: Path) -> None:
    """无 node_modules：npm ci 执行一次，成功后写入指纹 stamp。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert _npm_ci_count(stub_log) == 1
    stamp = main / "frontend" / "node_modules" / ".deps-stamp"
    assert stamp.is_file()
    assert len(stamp.read_text().strip()) > 0


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
    """npm ci 失败：非零退出、不写 stamp（半失败态不留「已装」假象）；
    下次成功后照常安装——stamp 只在成功后写，中断天然自愈。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    failed = _run(main, bin_dir, stub_log, {"STUB_NPM_RC": "1"})
    assert failed.returncode != 0
    assert not (main / "frontend" / "node_modules" / ".deps-stamp").exists()

    healed = _run(main, bin_dir, stub_log)
    assert healed.returncode == 0, healed.stderr
    assert _npm_ci_count(stub_log) == 2


def test_missing_manifest_fails_fast(tmp_path: Path) -> None:
    """清单文件缺失：fail-fast 且指明缺哪个，不执行 npm ci。"""
    main, bin_dir = _setup(tmp_path)
    (main / "frontend" / "package-lock.json").unlink()
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 1
    assert "frontend/package-lock.json" in result.stderr
    assert not stub_log.exists()


def test_stamp_lives_inside_node_modules() -> None:
    """stamp 必须放在 node_modules 内：手动删目录时 stamp 随之消失、
    天然失效，不存在「目录已删但 stamp 还说已装」的漂移。"""
    assert 'STAMP="frontend/node_modules/.deps-stamp"' in SCRIPT_TEXT


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
