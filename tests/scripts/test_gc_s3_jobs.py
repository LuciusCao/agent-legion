"""Contract tests for scripts/gc-s3-jobs.py.

CLI 编排层的契约：dotenv 加载、未配置 S3 的干净跳过、dry-run/--apply 两档
输出。扫描/删除判定逻辑已在 tests/services/test_s3_jobs_gc.py 直测，这里用
PYTHONPATH 探针桩替掉 boto3 与 storage settings，DB 侧桩掉
server.app.settings.load_settings（不触真 Postgres）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gc-s3-jobs.py"

_DOTENV_PROBE = """
import os


def load_dotenv(dotenv_path=None, override=False):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"load_dotenv {dotenv_path}\\n")
    return True
"""

# boto3 桩：ListObjectsV2 分页器对任何 Prefix 都返回空集（对象列举交给
# s3_jobs_gc 桩的固定返回），delete_objects 记入 STUB_LOG 并返回全成功。
_BOTO3_PROBE = """
import os


class _Paginator:
    def paginate(self, Bucket, Prefix=None):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"list_objects {Bucket} prefix={Prefix}\\n")
        yield {"Contents": []}


class _Client:
    def get_paginator(self, op):
        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"delete_objects {Bucket} n={len(Delete['Objects'])}\\n")
        return {"Errors": []}


def client(*args, **kwargs):
    return _Client()
"""


# s3_jobs_gc 桩：scan_orphans/apply_gc 的行为由 env 驱动并记入 STUB_LOG，
# CLI 编排契约（参数、输出、dry-run/--apply 分档）不依赖真判定逻辑——后者
# 已在 tests/services/test_s3_jobs_gc.py 直测。桩经 STUB_*_MODULE 机制注入。
_S3_JOBS_GC_PROBE = """
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DEFAULT_GRACE_HOURS = 24.0
DEFAULT_STAGING_GRACE_HOURS = 24.0


@dataclass(frozen=True)
class ObjectEntry:
    key: str
    last_modified: datetime
    size_bytes: int


@dataclass
class OrphanReport:
    authority_orphans: list
    staging_orphans: list

    @property
    def count(self):
        return len(self.authority_orphans) + len(self.staging_orphans)


def scan_orphans(lister, key_exists, *, grace_hours=24.0, staging_grace_hours=24.0, now=None):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(
            f"scan_orphans grace={grace_hours} staging_grace={staging_grace_hours}\\n"
        )
    # 真判定逻辑不在此重放：固定返回 1+1，供 CLI 输出断言。
    t = datetime.now(UTC)
    return OrphanReport(
        authority_orphans=[ObjectEntry("jobs/x/a.json", t, 50)],
        staging_orphans=[ObjectEntry("jobs-staging/x/b.json", t, 30)],
    )


def make_db_key_existence(database_dsn):
    def exists(keys):
        return set(keys)

    return exists


def apply_gc(client, bucket, report):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write("apply_gc\\n")
    return len(report.authority_orphans) + len(report.staging_orphans)
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

# settings 桩：load_settings 返回带 database_url/jobs_dir 的 SimpleNamespace；
# 真实 DB 从不被触碰——s3_jobs_gc 桩的 make_db_key_existence 不走真实查询。
_SETTINGS_PROBE = """
from types import SimpleNamespace


def load_settings(*args, **kwargs):
    return SimpleNamespace(
        database_url="postgresql://stub/stub",
        data_dir=__import__("pathlib").Path("."),
        jobs_dir=__import__("pathlib").Path("jobs"),
    )
"""

# JobQueries 桩：CLI 只把它透传给 make_db_key_existence（同样被桩掉），
# 构造函数不得触碰 DB（真实 JobQueries.__init__ 会跑 init_db 建表）。
_JOBS_QUERIES_PROBE = """
class JobQueries:
    def __init__(self, path, jobs_dir=None):
        self.path = path
        self.jobs_dir = jobs_dir
"""


def _make_pystub(tmp_path: Path, storage_probe: str) -> Path:
    pystub = tmp_path / "pystub"
    (pystub / "botocore").mkdir(parents=True)
    (pystub / "server/app/storage").mkdir(parents=True)
    (pystub / "server/app/services").mkdir(parents=True)
    (pystub / "server/app/jobs/queries").mkdir(parents=True)
    (pystub / "server/app/settings.py").write_text(_SETTINGS_PROBE)
    (pystub / "server/app/jobs/__init__.py").write_text("")
    (pystub / "server/app/jobs/queries/__init__.py").write_text(_JOBS_QUERIES_PROBE)
    (pystub / "dotenv.py").write_text(_DOTENV_PROBE)
    (pystub / "boto3.py").write_text(_BOTO3_PROBE)
    (pystub / "botocore/__init__.py").write_text("")
    (pystub / "botocore/exceptions.py").write_text("")
    (pystub / "server/__init__.py").write_text("")
    (pystub / "server/app/__init__.py").write_text("")
    (pystub / "server/app/storage/__init__.py").write_text(storage_probe)
    (pystub / "server/app/services/__init__.py").write_text("")
    (pystub / "server/app/services/s3_jobs_gc.py").write_text(_S3_JOBS_GC_PROBE)
    return pystub


def _run(
    tmp_path: Path,
    pystub: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    stub_log = tmp_path / "stub.log"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(pystub),
        "STUB_LOG": str(stub_log),
    }
    env.update(env_extra or {})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    log = stub_log.read_text() if stub_log.exists() else ""
    return result, log


def test_unconfigured_store_is_a_clean_skip(tmp_path: Path) -> None:
    """未配置 AGENT_LEGION_S3_BUCKET：提示并退出 0，不触碰 boto3。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_UNCONFIGURED)

    result, log = _run(tmp_path, pystub)

    assert result.returncode == 0, result.stderr
    assert "未配置" in result.stdout
    assert "list_objects" not in log


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """dry-run：打印桩报告的孤儿计数，不调用 apply/delete_objects。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub)

    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "jobs/ 孤儿 1 个" in result.stdout
    assert "jobs-staging/ 孤儿 1 个" in result.stdout
    assert "apply_gc" not in log
    assert "delete_objects" not in log


def test_apply_deletes_reported_orphans(tmp_path: Path):
    """--apply：调用 apply_gc 并打印删除数。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, "--apply")

    assert result.returncode == 0, result.stderr
    assert "已删除 2 个" in result.stdout
    assert "apply_gc" in log


def test_grace_args_reach_scan(tmp_path: Path):
    """--grace-hours/--staging-grace-hours 原样传给 scan_orphans。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, "--grace-hours", "48", "--staging-grace-hours", "6")

    assert result.returncode == 0, result.stderr
    assert "scan_orphans grace=48.0 staging_grace=6.0" in log
