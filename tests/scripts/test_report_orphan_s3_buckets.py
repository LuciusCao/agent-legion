"""Contract tests for scripts/report-orphan-s3-buckets.py.

The script is a thin orchestration over dotenv + boto3 + git +
server.app.storage.load_s3_settings; tests run it as a subprocess with probe
stub modules earlier on PYTHONPATH (same pattern as test_ensure_s3_bucket.py).
The git subprocess itself is stubbed via a PATH-shim binary so no real
worktree metadata is read. No real S3 endpoint or .env is touched.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report-orphan-s3-buckets.py"

_DOTENV_PROBE = """
import os


def load_dotenv(dotenv_path=None, override=False):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"load_dotenv {dotenv_path}\\n")
    return True
"""

# boto3 桩：ListBuckets / ListObjectsV2（分页器）由 env 驱动，调用记入 STUB_LOG。
_BOTO3_PROBE = """
import os


class _Paginator:
    def __init__(self, objects):
        self._objects = objects

    def paginate(self, Bucket, Prefix=None):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"list_objects {Bucket} prefix={Prefix}\\n")
        yield {"Contents": [
            {"Key": o[0], "Size": o[1]} for o in self._objects
        ]}


class _Client:
    def list_buckets(self):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write("list_buckets\\n")
        names = os.environ.get("STUB_BUCKETS", "").split(",")
        return {"Buckets": [{"Name": n} for n in names if n]}

    def get_paginator(self, op):
        objects = [
            tuple(o.split(":"))
            for o in os.environ.get("STUB_ORPHAN_OBJECTS", "").split(",")
            if o
        ]
        return _Paginator(objects)


def client(*args, **kwargs):
    return _Client()
"""

_STORAGE_PROBE_CONFIGURED = """
from types import SimpleNamespace


def load_s3_settings():
    return SimpleNamespace(
        bucket="agent-legion-dev",
        endpoint_url="http://127.0.0.1:9000",
        region="us-east-1",
        access_key="",
        secret_key="",
        public_endpoint_url="",
    )
"""

_STORAGE_PROBE_UNCONFIGURED = """
def load_s3_settings():
    return None
"""

# git 桩：cwd 无关，固定输出两行 worktree（bare 主仓 + develop）。
_GIT_SHIM = """#!/bin/sh
echo "worktree /repo"
echo "branch refs/heads/main"
echo "worktree /repo/.worktrees/develop"
echo "branch refs/heads/develop"
"""


def _make_pystub(tmp_path: Path, storage_probe: str) -> Path:
    pystub = tmp_path / "pystub"
    (pystub / "botocore").mkdir(parents=True)
    (pystub / "server/app/storage").mkdir(parents=True)
    (pystub / "dotenv.py").write_text(_DOTENV_PROBE)
    (pystub / "boto3.py").write_text(_BOTO3_PROBE)
    (pystub / "botocore/__init__.py").write_text("")
    (pystub / "botocore/exceptions.py").write_text("")
    (pystub / "server/__init__.py").write_text("")
    (pystub / "server/app/__init__.py").write_text("")
    (pystub / "server/app/storage/__init__.py").write_text(storage_probe)
    return pystub


def _make_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_shim = bin_dir / "git"
    git_shim.write_text(_GIT_SHIM)
    git_shim.chmod(git_shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(
    tmp_path: Path,
    pystub: Path,
    bin_dir: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stub_log = tmp_path / "stub.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(pystub),
        "STUB_LOG": str(stub_log),
    }
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_unconfigured_store_is_a_clean_skip(tmp_path: Path) -> None:
    """未配置 AGENT_LEGION_S3_BUCKET：提示并退出 0，不触碰 boto3。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_UNCONFIGURED)
    bin_dir = _make_bin(tmp_path)

    result = _run(tmp_path, pystub, bin_dir, ".env")

    assert result.returncode == 0, result.stderr
    assert "未配置" in result.stdout
    assert "list_buckets" not in (tmp_path / "stub.log").read_text()


def test_reports_orphans_with_reclaim_hint(tmp_path: Path) -> None:
    """无对应 worktree 的派生 bucket 被列出，附 clean-worktree.sh 收尾命令。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)
    bin_dir = _make_bin(tmp_path)

    # 现存 worktree: develop（主 bucket agent-legion-dev 被排除，不列）。
    # 其余全是孤儿：老派生 bucket + 非派生命名 bucket（护栏必须排除）。
    result = _run(
        tmp_path,
        pystub,
        bin_dir,
        ".env",
        env_extra={
            "STUB_BUCKETS": (
                "agent-legion-dev,agent-legion-retired-one,some-other-bucket,agent-legion-"
            ),
            "STUB_ORPHAN_OBJECTS": "a.json:100,b.json:50",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "agent-legion-retired-one" in result.stdout
    assert "2 个对象" in result.stdout
    assert "150 字节" in result.stdout
    assert "clean-worktree.sh retired-one --yes" in result.stdout
    # 护栏：主 bucket、非派生 bucket、裸前缀都不进报告。
    assert "some-other-bucket" not in result.stdout
    assert "已配置" not in result.stdout  # 主 bucket 不是「孤儿」措辞出现


def test_zero_orphans_reports_clean(tmp_path: Path) -> None:
    """全部派生 bucket 有对应 worktree：报 0 孤儿，不列对象。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)
    bin_dir = _make_bin(tmp_path)

    result = _run(
        tmp_path,
        pystub,
        bin_dir,
        ".env",
        env_extra={"STUB_BUCKETS": "agent-legion-dev,agent-legion-develop"},
    )

    assert result.returncode == 0, result.stderr
    assert "0 个" in result.stdout
    assert "list_objects" not in (tmp_path / "stub.log").read_text()
