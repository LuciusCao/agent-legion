"""Contract tests for scripts/ensure-s3-bucket.py.

The script is a thin orchestration over dotenv + boto3 +
server.app.storage.load_s3_settings; tests run it as a subprocess with probe
stub modules earlier on PYTHONPATH, so no real S3 endpoint or .env is
touched. Stub behavior is driven by env vars (STUB_HEAD_ERROR) and recorded
into STUB_LOG for assertions.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-s3-bucket.py"

_DOTENV_PROBE = """
import os


def load_dotenv(dotenv_path=None, override=False):
    if dotenv_path is None:
        raise AssertionError("bare load_dotenv() crashes under `python -` stdin")
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"load_dotenv {dotenv_path}\\n")
    return True
"""

_BOTO3_PROBE = """
import os

from botocore.exceptions import ClientError


class _Client:
    def head_bucket(self, Bucket):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"head_bucket {Bucket}\\n")
        code = os.environ.get("STUB_HEAD_ERROR", "")
        if code:
            raise ClientError({"Error": {"Code": code}}, "HeadBucket")

    def create_bucket(self, Bucket):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"create_bucket {Bucket}\\n")

    def put_bucket_cors(self, Bucket, CORSConfiguration):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"put_bucket_cors {Bucket}\\n")


def client(*args, **kwargs):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"client {kwargs.get('endpoint_url', '-')}\\n")
    return _Client()
"""

_BOTOCORE_EXCEPTIONS_PROBE = """
class ClientError(Exception):
    def __init__(self, response=None, operation_name=""):
        super().__init__(response)
        self.response = response or {}
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


def _make_pystub(tmp_path: Path, storage_probe: str) -> Path:
    pystub = tmp_path / "pystub"
    (pystub / "botocore").mkdir(parents=True)
    (pystub / "server/app/storage").mkdir(parents=True)
    (pystub / "dotenv.py").write_text(_DOTENV_PROBE)
    (pystub / "boto3.py").write_text(_BOTO3_PROBE)
    (pystub / "botocore/__init__.py").write_text("")
    (pystub / "botocore/exceptions.py").write_text(_BOTOCORE_EXCEPTIONS_PROBE)
    (pystub / "server/__init__.py").write_text("")
    (pystub / "server/app/__init__.py").write_text("")
    (pystub / "server/app/storage/__init__.py").write_text(storage_probe)
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

    result, log = _run(tmp_path, pystub, ".env")

    assert result.returncode == 0, result.stderr
    assert "未配置" in result.stdout
    assert "client " not in log


def test_existing_bucket_skips_create_but_refreshes_cors(tmp_path: Path) -> None:
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, ".env")

    assert result.returncode == 0, result.stderr
    assert "已存在" in result.stdout
    assert "head_bucket agent-legion-dev" in log
    assert "create_bucket" not in log
    assert "put_bucket_cors agent-legion-dev" in log


def test_missing_bucket_is_created(tmp_path: Path) -> None:
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, ".env", env_extra={"STUB_HEAD_ERROR": "404"})

    assert result.returncode == 0, result.stderr
    assert "已创建 S3 bucket: agent-legion-dev" in result.stdout
    assert "create_bucket agent-legion-dev" in log
    assert "put_bucket_cors agent-legion-dev" in log


def test_non_404_head_error_propagates_as_nonzero_exit(tmp_path: Path) -> None:
    """403 等非「不存在」错误必须非零退出，由调用方决定降级。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, ".env", env_extra={"STUB_HEAD_ERROR": "403"})

    assert result.returncode != 0
    assert "create_bucket" not in log


def test_env_file_arg_is_passed_to_load_dotenv(tmp_path: Path) -> None:
    """load_dotenv 必须显式拿到 env 文件路径（无参会走 find_dotenv 调用栈
    探测）；位置参数缺省为 .env，显式传参按传入值。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_UNCONFIGURED)

    result, log = _run(tmp_path, pystub)
    assert result.returncode == 0, result.stderr
    assert "load_dotenv .env" in log

    result, log = _run(tmp_path, pystub, "custom.env")
    assert result.returncode == 0, result.stderr
    assert "load_dotenv custom.env" in log
