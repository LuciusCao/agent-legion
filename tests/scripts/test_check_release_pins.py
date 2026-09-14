"""Contract tests for scripts/check_release_pins.py.

纯文件 fixture（无需 git）：仓库根下摆好两个基准清单与四个钉点文件，
钉点新鲜 / 过期 / 缺失分别断言。fixture 内容取自真实文件的形态片段，
钉点形态即契约——门禁失配（fail-closed）本身就是被测行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts.check_release_pins import pin_errors

pytestmark = pytest.mark.no_db

INSTALLER = """\
WORKER_VERSION_DEFAULT="0.7.0"
VELITES_VERSION_DEFAULT="0.5.1"
WORKER_VERSION="${AGENT_WORKER_VERSION:-$WORKER_VERSION_DEFAULT}"
"""

STANDALONE = """\
services:
  worker:
    image: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.7.0}
    environment:
      AGENT_WORKER_IMAGE_VERSION: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.7.0}
"""

PULL_EXAMPLE = """\
services:
  worker:
    build: !reset null
    image: ghcr.io/luciuscao/agent-legion-worker:0.7.0
    environment:
      AGENT_WORKER_IMAGE_VERSION: ghcr.io/luciuscao/agent-legion-worker:0.7.0
"""


def _write_repo(
    root: Path,
    *,
    repo_version: str = "0.7.0",
    velites_version: str = "0.5.1",
    installer: str = INSTALLER,
    standalone: str = STANDALONE,
    pull_example: str = PULL_EXAMPLE,
) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agent-legion"\nversion = "{repo_version}"\n', encoding="utf-8"
    )
    velites = root / "velites"
    velites.mkdir(parents=True)
    (velites / "Cargo.toml").write_text(
        f'[package]\nname = "velites"\nversion = "{velites_version}"\n', encoding="utf-8"
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "install-worker.sh").write_text(installer, encoding="utf-8")
    deploy = root / "deploy"
    deploy.mkdir()
    (deploy / "compose.worker.standalone.yaml").write_text(standalone, encoding="utf-8")
    (deploy / "compose.worker.pull.example.yaml").write_text(pull_example, encoding="utf-8")


def test_fresh_pins_pass(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    errors, notes = pin_errors(tmp_path)
    assert errors == []
    assert len([note for note in notes if "✓" in note]) == 4


def test_stale_installer_worker_pin_is_rejected(tmp_path: Path) -> None:
    _write_repo(
        tmp_path,
        installer=INSTALLER.replace(
            'WORKER_VERSION_DEFAULT="0.7.0"', 'WORKER_VERSION_DEFAULT="0.6.1"'
        ),
    )
    errors, _ = pin_errors(tmp_path)
    assert any("WORKER_VERSION_DEFAULT" in error and "0.6.1" in error for error in errors)


def test_stale_installer_velites_pin_is_rejected(tmp_path: Path) -> None:
    _write_repo(tmp_path, velites_version="0.5.2")
    errors, _ = pin_errors(tmp_path)
    assert any("VELITES_VERSION" in error and "0.5.1" in error for error in errors)


def test_stale_compose_pins_are_rejected(tmp_path: Path) -> None:
    stale = STANDALONE.replace("agent-legion-worker:0.7.0", "agent-legion-worker:0.6.0")
    _write_repo(tmp_path, standalone=stale)
    errors, _ = pin_errors(tmp_path)
    # 同文件两处钉点（image: 与 AGENT_WORKER_IMAGE_VERSION:）分别报错
    assert len([error for error in errors if "standalone" in error]) == 2


def test_prerelease_normalization_matches_tag_forms(tmp_path: Path) -> None:
    """PEP 440 预发布与 tag 形归一后等价（0.8.0a0 ↔ 0.8.0-alpha）。"""
    _write_repo(
        tmp_path,
        repo_version="0.8.0a0",
        installer=INSTALLER.replace(
            'WORKER_VERSION_DEFAULT="0.7.0"', 'WORKER_VERSION_DEFAULT="0.8.0-alpha"'
        ),
        standalone=STANDALONE.replace(
            "agent-legion-worker:0.7.0", "agent-legion-worker:0.8.0-alpha"
        ),
        pull_example=PULL_EXAMPLE.replace(
            "agent-legion-worker:0.7.0", "agent-legion-worker:0.8.0-alpha"
        ),
    )
    errors, _ = pin_errors(tmp_path)
    assert errors == []


def test_missing_pin_is_fail_closed(tmp_path: Path) -> None:
    """钉点从文件里消失（形态被改）必须报错，不允许静默失明。"""
    _write_repo(tmp_path, installer='WORKER_VERSION="${AGENT_WORKER_VERSION:-latest}"\n')
    errors, _ = pin_errors(tmp_path)
    assert any("钉点" in error and "check_release_pins.py" in error for error in errors)


def test_missing_compose_file_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "deploy/compose.worker.pull.example.yaml").unlink()
    errors, _ = pin_errors(tmp_path)
    assert any("读取" in error and "pull.example" in error for error in errors)


def test_missing_manifest_reports_unresolvable(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "velites/Cargo.toml").unlink()
    errors, _ = pin_errors(tmp_path)
    assert any("读取失败" in error for error in errors)


def test_velites_pin_is_exact_not_normalized(tmp_path: Path) -> None:
    """codex P2-1：velites 钉点逐字比较——0.8.0a0 钉成 0.8.0-alpha 不得
    靠等价归一静默通过（发布 tag 只会是 Cargo 逐字形态）。"""
    installer = INSTALLER.replace(
        'VELITES_VERSION_DEFAULT="0.5.1"', 'VELITES_VERSION_DEFAULT="0.5.1-alpha"'
    )
    _write_repo(tmp_path, installer=installer)
    errors, _ = pin_errors(tmp_path)
    assert any("VELITES_VERSION" in e for e in errors)


def test_worker_image_refix_suffix_is_accepted(tmp_path: Path) -> None:
    """codex P2-2：-rN 重发后缀是受支持的镜像 tag——默认值指向修复镜像
    （0.7.0-r2）不要求仓库版本同步改动。"""
    refixed = STANDALONE.replace(
        "agent-legion-worker:0.7.0}", "agent-legion-worker:0.7.0-r2}"
    ).replace(
        "AGENT_WORKER_IMAGE_VERSION: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.7.0-r2}",
        "AGENT_WORKER_IMAGE_VERSION: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.7.0-r2}",
    )
    _write_repo(tmp_path, standalone=refixed)
    errors, notes = pin_errors(tmp_path)
    assert errors == []
    assert any("standalone" in n and "0.7.0 ✓" in n for n in notes)


def test_compose_both_pins_required(tmp_path: Path) -> None:
    """codex P2-3：compose 的 image: 与 env 钉点缺一不可——删掉 env 一处
    不得因「剩一个匹配」而静默通过。"""
    missing_env = STANDALONE.replace(
        "      AGENT_WORKER_IMAGE_VERSION: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.7.0}\n",
        "",
    )
    _write_repo(tmp_path, standalone=missing_env)
    errors, _ = pin_errors(tmp_path)
    assert any("standalone" in e and "钉点" in e for e in errors)


def test_main_parses_argv_when_none(tmp_path: Path) -> None:
    """codex P2-4：`python -m` 调用走 argv=None——必须回落解析
    sys.argv[1:]，--root 不得被静默忽略。"""
    from scripts import check_release_pins

    _write_repo(tmp_path)
    stale = INSTALLER.replace('WORKER_VERSION_DEFAULT="0.7.0"', 'WORKER_VERSION_DEFAULT="0.6.0"')
    (tmp_path / "scripts" / "install-worker.sh").write_text(stale, encoding="utf-8")
    monkey_args = ["--root", str(tmp_path)]
    original_argv = sys.argv
    try:
        sys.argv = ["check_release_pins", *monkey_args]
        assert check_release_pins.main() != 0
    finally:
        sys.argv = original_argv
