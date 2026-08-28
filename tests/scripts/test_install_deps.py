"""Contract tests for scripts/install-deps.sh.

The script resolves ROOT from its own location, so tests copy it into a
synthetic repo layout and run it on a restricted PATH with stubbed
``uname``/``brew``/``uv``/``openssl``/etc.: no real package manager,
database, or vault key is touched. Tool invocations are recorded into
STUB_LOG for assertions.
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
SCRIPT = ROOT / "scripts" / "install-deps.sh"

_ENV_EXAMPLE = """\
AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
AGENT_LEGION_S3_ACCESS_KEY=
AGENT_LEGION_S3_SECRET_KEY=
"""

_WORKER_EXAMPLE = """\
host_url: http://agent-legion-host:8000
worker_id: worker-1
work_root: /var/lib/agent-legion-worker
"""

_UNAME_STUB = """#!/usr/bin/env bash
echo "${STUB_UNAME:-Darwin}"
"""

_BREW_STUB = """#!/usr/bin/env bash
echo "brew $*" >> "${STUB_LOG}"
if [[ "$1" == "--prefix" ]]; then echo "/stub-prefix"; exit 0; fi
if [[ "$1" == "list" ]]; then exit 1; fi
exit 0
"""

_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "${STUB_LOG}"
if [[ "$1" == "run" ]]; then
  echo "stub-vault-master-key"
fi
exit 0
"""

_OPENSSL_STUB = """#!/usr/bin/env bash
if [[ "$1" == "rand" && "$3" == "20" ]]; then
  echo "stub-access-key"
elif [[ "$1" == "rand" ]]; then
  echo "stub-secret-key"
else
  exit 1
fi
"""

_LOG_STUB = """#!/usr/bin/env bash
echo "{name} $*" >> "${{STUB_LOG}}"
exit 0
"""

_ENSURE_VELITES_STUB = """#!/usr/bin/env bash
echo "ensure-velites $*" >> "${STUB_LOG}"
exit 0
"""

# 版本预检桩：python3 -c / node -e 退出 0 即视为版本达标。
_EXIT_OK_STUB = """#!/usr/bin/env bash
exit 0
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """Lay out a synthetic repo root with the script + seeded inputs; stub bin."""
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "config").mkdir()
    (main / "frontend").mkdir()
    shutil.copy(SCRIPT, main / "scripts" / SCRIPT.name)
    _write_stub(main / "scripts" / "ensure-velites.sh", _ENSURE_VELITES_STUB)
    (main / ".env.example").write_text(_ENV_EXAMPLE)
    (main / "config" / "agent-worker.example.yaml").write_text(_WORKER_EXAMPLE)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "uname", _UNAME_STUB)
    _write_stub(bin_dir / "brew", _BREW_STUB)
    _write_stub(bin_dir / "uv", _UV_STUB)
    _write_stub(bin_dir / "openssl", _OPENSSL_STUB)
    for tool in ("python3", "node", "psql", "createdb", "docker", "npm", "pg_isready"):
        _write_stub(bin_dir / tool, _LOG_STUB.format(name=tool))
    return main, bin_dir


def _run(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "STUB_LOG": str(stub_log),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(main / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_non_macos_fails_fast_with_guidance(tmp_path: Path) -> None:
    """非 macOS：打印安装指引后 fail-fast，且无任何副作用。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_UNAME": "Linux"})

    assert result.returncode == 1
    assert "缺少前置依赖" in result.stderr
    assert not (main / ".env").exists()
    assert not (main / "deploy").exists()


def test_macos_all_tools_present_initializes(tmp_path: Path) -> None:
    """macOS + 工具齐备：不走 brew install，完成 .env/凭据/vault/worker 种子。"""
    main, bin_dir = _setup(tmp_path)
    _write_stub(bin_dir / "cargo", _EXIT_OK_STUB)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    log = stub_log.read_text()
    assert "brew install" not in log
    assert "uv sync" in log
    assert "createdb agent_legion" in log
    assert "ensure-velites --dest data/bin" in log
    env_text = (main / ".env").read_text()
    assert "AGENT_LEGION_S3_ACCESS_KEY=stub-access-key" in env_text
    assert "AGENT_LEGION_S3_SECRET_KEY=stub-secret-key" in env_text
    assert (main / "deploy/secrets/vault_master_key").read_text().strip() == (
        "stub-vault-master-key"
    )
    config = (main / "config/agent-worker.yaml").read_text()
    assert "host_url: http://127.0.0.1:8001" in config
    assert "work_root: data/agent-worker" in config
    assert "make dev-up" in result.stdout


def test_macos_missing_tool_installed_via_brew(tmp_path: Path) -> None:
    """macOS 缺 cargo：经 brew install rust 补装，其余步骤照常。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert "brew install rust" in stub_log.read_text()
    assert (main / ".env").exists()


def test_rerun_is_idempotent_and_keeps_existing_env(tmp_path: Path) -> None:
    """重跑幂等：已有 .env / vault key / worker 配置不被覆盖。"""
    main, bin_dir = _setup(tmp_path)
    _write_stub(bin_dir / "cargo", _EXIT_OK_STUB)
    stub_log = tmp_path / "stub.log"

    first = _run(main, bin_dir, stub_log)
    assert first.returncode == 0, first.stderr
    env_before = (main / ".env").read_text()
    key_before = (main / "deploy/secrets/vault_master_key").read_text()

    second = _run(main, bin_dir, stub_log)

    assert second.returncode == 0, second.stderr
    assert ".env 已存在" in second.stdout
    assert "config/agent-worker.yaml 已存在" in second.stdout
    assert (main / ".env").read_text() == env_before
    assert (main / "deploy/secrets/vault_master_key").read_text() == key_before
