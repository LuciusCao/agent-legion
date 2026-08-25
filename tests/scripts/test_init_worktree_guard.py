"""Contract tests for scripts/init-worktree.sh nested-worktree guard.

The script resolves ROOT from its own location, so tests copy it into a
synthetic repo layout and run it with stubbed ``git``/``uv`` on a restricted
PATH: no real repo, database, or vault key is touched.
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
SCRIPT = ROOT / "scripts" / "init-worktree.sh"

_GIT_STUB = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree {main}"
  echo "bare"
  echo
  echo "worktree {main}/.worktrees/develop"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/develop"
  exit 0
fi
echo "unexpected git call: $*" >&2
exit 1
"""

_UV_STUB = """#!/usr/bin/env bash
echo "AGENT_LEGION_DATABASE_URL=${AGENT_LEGION_DATABASE_URL-<unset>}" >> "${STUB_LOG:-/dev/null}"
echo "stub-vault-master-key"
"""

# 主仓库根非 bare（普通 checkout，无 .env）的布局加固场景：git worktree list
# 第一条不带 bare 行时，基准选择也必须跳过主仓库根。
_GIT_STUB_NONBARE_MAIN = """#!/usr/bin/env bash
if [[ "$1" == "worktree" && "$2" == "list" ]]; then
  echo "worktree {main}"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/main"
  echo
  echo "worktree {main}/.worktrees/develop"
  echo "HEAD 0000000000000000000000000000000000000000"
  echo "branch refs/heads/develop"
  exit 0
fi
echo "unexpected git call: $*" >&2
exit 1
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, script_rel: str, git_stub: str = _GIT_STUB) -> tuple[Path, Path]:
    """Lay out main/.worktrees/... with the script at script_rel; stub bin dir."""
    main = tmp_path / "main"
    script_path = main / script_rel
    script_path.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "git", git_stub.format(main=main))
    _write_stub(bin_dir / "uv", _UV_STUB)
    return main, bin_dir


def _run(
    script_path: Path,
    bin_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_nested_worktree_is_rejected(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, ".worktrees/dev/.worktrees/nested/scripts/init-worktree.sh")
    script_path = main / ".worktrees/dev/.worktrees/nested/scripts/init-worktree.sh"

    result = _run(script_path, bin_dir)

    assert result.returncode == 1
    assert "禁止嵌套" in result.stderr
    assert str(main) in result.stderr
    # Guard fires before any side effect.
    assert not (main / ".worktrees/dev/.worktrees/nested/deploy").exists()


def test_flat_worktree_passes_guard_and_initializes(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    # 主仓库是 bare，.env 从第一个非 bare 的基准 worktree 复制。
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")
    script_path = main / ".worktrees/flat/scripts/init-worktree.sh"

    result = _run(script_path, bin_dir)

    assert result.returncode == 0, result.stderr
    worktree = main / ".worktrees/flat"
    assert (
        "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat"
        in (worktree / ".env").read_text()
    )
    # issue #35：全局 register token 已退役，init-worktree 不再生成。
    assert not (worktree / "deploy/secrets/agent_worker_register_token").exists()
    assert (worktree / "deploy/secrets/vault_master_key").read_text().strip() == (
        "stub-vault-master-key"
    )


def test_main_repo_root_exits_without_side_effects(tmp_path: Path) -> None:
    main, bin_dir = _setup(tmp_path, "scripts/init-worktree.sh")

    result = _run(main / "scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 0
    assert "无需初始化" in result.stderr
    assert not (main / "deploy").exists()


def test_worker_config_seeded_from_base_with_rewritten_identity(tmp_path: Path) -> None:
    """缺失的 config/agent-worker.yaml 从基准复制并改写本实例字段。"""
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")
    (develop / "config").mkdir()
    (develop / "config" / "agent-worker.yaml").write_text(
        "host_url: http://127.0.0.1:8000\n"
        "worker_id: base-worker\n"
        "name: Base Worker\n"
        "runtimes: [velites]\n"
        "register_token_file: /run/secrets/agent_worker_register_token\n",
        encoding="utf-8",
    )

    result = _run(main / ".worktrees/flat/scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 0, result.stderr
    worktree = main / ".worktrees/flat"
    config = (worktree / "config/agent-worker.yaml").read_text()
    assert "host_url: http://127.0.0.1:8001" in config
    assert "worker_id: flat" in config
    assert "name: flat (worktree)" in config
    assert "runtimes: [velites]" in config
    # issue #35：token 不再经配置文件注入（原容器路径行保留原样，注册走
    # worker 控制台粘贴 scoped token）。
    assert "register_token_file: /run/secrets/agent_worker_register_token" in config


def test_worker_config_host_url_uses_dev_backend_port(tmp_path: Path) -> None:
    """host_url 端口跟随 DEV_BACKEND_PORT（与 make dev-backend 一致）。"""
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")
    (develop / "config").mkdir()
    (develop / "config" / "agent-worker.yaml").write_text(
        "host_url: http://127.0.0.1:8000\n",
        encoding="utf-8",
    )

    result = _run(
        main / ".worktrees/flat/scripts/init-worktree.sh",
        bin_dir,
        extra_env={"DEV_BACKEND_PORT": "8010"},
    )

    assert result.returncode == 0, result.stderr
    config = (main / ".worktrees/flat/config/agent-worker.yaml").read_text()
    assert "host_url: http://127.0.0.1:8010" in config


def test_nonbare_main_repo_is_skipped_as_base(tmp_path: Path) -> None:
    """主仓库根非 bare（普通 checkout、无 .env）时不得作为 .env 复制基准。

    加固场景：2026-08-18 事故的实际路径是基准 worktree 缺 .env（由
    fail-fast 兜底），本测试覆盖「主仓库根非 bare 被误选为基准」这一变体。
    """
    main, bin_dir = _setup(
        tmp_path, ".worktrees/flat/scripts/init-worktree.sh", _GIT_STUB_NONBARE_MAIN
    )
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")

    result = _run(main / ".worktrees/flat/scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 0, result.stderr
    env_text = (main / ".worktrees/flat/.env").read_text()
    assert "# stub env" in env_text  # 复制自 develop 而非主仓库根
    assert "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_flat" in env_text


def test_s3_bucket_derived_from_worktree_name(tmp_path: Path) -> None:
    """.env 缺 AGENT_LEGION_S3_BUCKET 时按 worktree 名派生并写入（幂等）。"""
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")
    script_path = main / ".worktrees/flat/scripts/init-worktree.sh"

    result = _run(script_path, bin_dir)
    assert result.returncode == 0, result.stderr
    env_text = (main / ".worktrees/flat/.env").read_text()
    assert "AGENT_LEGION_S3_BUCKET=agent-legion-flat" in env_text

    # 重跑不重复追加。
    result = _run(script_path, bin_dir)
    assert result.returncode == 0, result.stderr
    env_text = (main / ".worktrees/flat/.env").read_text()
    assert env_text.count("AGENT_LEGION_S3_BUCKET=") == 1


def test_s3_bucket_existing_value_is_rewritten(tmp_path: Path) -> None:
    """.env 已含 AGENT_LEGION_S3_BUCKET（从基准复制而来）时改写为派生名。

    .env 复制自基准 worktree，本就带着基准的 bucket；保留原值会让所有
    派生 worktree 共享基准 bucket，违背 per-worktree 隔离——与
    AGENT_LEGION_DATABASE_URL 同一模式，无条件改写为派生值。
    """
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("AGENT_LEGION_S3_BUCKET=custom-bucket\n")

    result = _run(main / ".worktrees/flat/scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 0, result.stderr
    env_text = (main / ".worktrees/flat/.env").read_text()
    assert "AGENT_LEGION_S3_BUCKET=agent-legion-flat" in env_text
    assert "custom-bucket" not in env_text


def test_missing_env_fails_fast_without_side_effects(tmp_path: Path) -> None:
    """无法复制 .env 时 fail-fast：缺 .env 会让后端回落共享默认库（prod）。

    且 fail-fast 必须在建库/生成 secrets 等副作用之前触发。
    """
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    # bare 主仓库 + develop 无 .env：无基准可复制。
    (main / ".worktrees/develop").mkdir(parents=True)

    result = _run(main / ".worktrees/flat/scripts/init-worktree.sh", bin_dir)

    assert result.returncode == 1
    assert "错误" in result.stderr
    assert "prod" in result.stderr
    worktree = main / ".worktrees/flat"
    assert not (worktree / ".env").exists()
    assert not (worktree / "deploy").exists()


# S3 建桶块的 heredoc 桩：uv stub 把 `uv run python -` 转给真实 python3，
# PYTHONPATH 前置探针模块——dotenv 桩对无参 load_dotenv() 抛
# AssertionError（复现 find_dotenv 在 stdin heredoc 下的崩溃），boto3 桩
# 把 head_bucket 记进 STUB_LOG 作为「越过 load_dotenv」的证据。
_DOTENV_PROBE = """
def load_dotenv(dotenv_path=None, override=False):
    if dotenv_path is None:
        raise AssertionError("bare load_dotenv() crashes under `python -` stdin")
    return True
"""

_BOTO3_PROBE = """
import os


class _Client:
    def head_bucket(self, Bucket):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"head_bucket {Bucket}\\n")

    def create_bucket(self, Bucket):
        pass

    def put_bucket_cors(self, Bucket, CORSConfiguration):
        pass


def client(*args, **kwargs):
    return _Client()
"""

_BOTOCORE_EXCEPTIONS_PROBE = """
class ClientError(Exception):
    def __init__(self, response=None, operation_name=""):
        super().__init__(response)
        self.response = response or {}
"""

_STORAGE_PROBE = """
from types import SimpleNamespace


def load_s3_settings():
    return SimpleNamespace(
        bucket="agent-legion-flat",
        endpoint_url="http://stub",
        region="us-east-1",
        access_key="",
        secret_key="",
        public_endpoint_url="",
    )
"""

_UV_STUB_REAL_HEREDOC = """#!/usr/bin/env bash
if [[ "$1" == "run" && "$2" == "python" && "$3" == "-" ]]; then
  PYTHONPATH="{pystub}:${{PYTHONPATH:-}}" exec python3 -
fi
echo "stub-vault-master-key"
"""


def test_s3_bucket_step_loads_dotenv_with_explicit_path(tmp_path: Path) -> None:
    """回归：S3 建桶块的 load_dotenv 必须显式传路径。

    无参 load_dotenv() 走 find_dotenv 的调用栈探测，在脚本的 stdin
    heredoc（uv run python - <<PY）模式下必抛 AssertionError，被外层
    降级吞成「endpoint 不可达」的误导性提示（2026-08-24 实测）。本测试
    用真实 python3 执行 heredoc + 探针桩，断言执行确实越过 load_dotenv
    到达 head_bucket。
    """
    main, bin_dir = _setup(tmp_path, ".worktrees/flat/scripts/init-worktree.sh")
    develop = main / ".worktrees/develop"
    develop.mkdir(parents=True)
    (develop / ".env").write_text("# stub env\n")
    pystub = tmp_path / "pystub"
    (pystub / "botocore").mkdir(parents=True)
    (pystub / "server/app/storage").mkdir(parents=True)
    (pystub / "dotenv.py").write_text(_DOTENV_PROBE)
    (pystub / "boto3.py").write_text(_BOTO3_PROBE)
    (pystub / "botocore/__init__.py").write_text("")
    (pystub / "botocore/exceptions.py").write_text(_BOTOCORE_EXCEPTIONS_PROBE)
    (pystub / "server/__init__.py").write_text("")
    (pystub / "server/app/__init__.py").write_text("")
    (pystub / "server/app/storage/__init__.py").write_text(_STORAGE_PROBE)
    _write_stub(bin_dir / "uv", _UV_STUB_REAL_HEREDOC.format(pystub=pystub))
    stub_log = tmp_path / "stub.log"

    result = _run(
        main / ".worktrees/flat/scripts/init-worktree.sh",
        bin_dir,
        extra_env={"STUB_LOG": str(stub_log)},
    )

    assert result.returncode == 0, result.stderr
    assert "head_bucket agent-legion-flat" in stub_log.read_text()
