"""ensure-s3-bucket.py 的 CORS 读失败语义（#1111）：真实 botocore Stubber。

put_bucket_cors 是整份替换。只有 NoSuchCORSConfiguration（bucket 尚无 CORS）
可以当空规则继续；AccessDenied / 5xx 等读失败必须非零退出且不写——否则
只含 dev origin 的规则会覆盖掉既有 prod origin。与 test_ensure_s3_bucket.py
的子进程探针互补：这里用真实 botocore 的错误形态（Stubber 生成的
ClientError），在进程内执行脚本 main()。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-s3-bucket.py"
BUCKET = "agent-legion-dev"
PROD_ORIGIN = "https://app.example.com"
DEV_ORIGINS = sorted(
    f"http://{host}:{port}" for port in ("5173", "5174") for host in ("127.0.0.1", "localhost")
)


def _load_main() -> Any:
    spec = importlib.util.spec_from_file_location("ensure_s3_bucket_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def _rule(origins: list[str]) -> dict[str, Any]:
    return {
        "AllowedOrigins": origins,
        "AllowedMethods": ["PUT", "GET", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Stubber, Any]:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:9",
        aws_access_key_id="ak",
        aws_secret_access_key="sk",
    )
    stubber = Stubber(client)
    settings = SimpleNamespace(
        bucket=BUCKET,
        endpoint_url="http://127.0.0.1:9",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
    )
    monkeypatch.setattr("server.app.storage.load_s3_settings", lambda: settings)
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr("sys.argv", ["ensure-s3-bucket.py", str(tmp_path / "absent.env")])
    monkeypatch.delenv("DEV_FRONTEND_PORT", raising=False)
    stubber.add_response("head_bucket", {}, {"Bucket": BUCKET})
    return stubber, _load_main()


def test_missing_cors_configuration_creates_rules(s3: tuple[Stubber, Any]) -> None:
    stubber, main = s3
    stubber.add_client_error(
        "get_bucket_cors", service_error_code="NoSuchCORSConfiguration", http_status_code=404
    )
    stubber.add_response(
        "put_bucket_cors",
        {},
        {"Bucket": BUCKET, "CORSConfiguration": {"CORSRules": [_rule(DEV_ORIGINS)]}},
    )
    with stubber:
        assert main() == 0
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("code", "status"),
    [("AccessDenied", 403), ("InternalError", 500), ("SlowDown", 503)],
)
def test_other_read_failures_exit_without_writing(
    s3: tuple[Stubber, Any], code: str, status: int
) -> None:
    stubber, main = s3
    stubber.add_client_error("get_bucket_cors", service_error_code=code, http_status_code=status)
    # 没有为 put_bucket_cors 排队应答：脚本若仍去写，Stubber 会报
    # UnStubbedResponseError（AssertionError 族），不是本断言期望的 ClientError。
    with stubber, pytest.raises(ClientError) as caught:
        main()
    assert caught.value.response["Error"]["Code"] == code


def test_existing_rules_are_kept_when_merging(s3: tuple[Stubber, Any]) -> None:
    stubber, main = s3
    existing = _rule([PROD_ORIGIN])
    stubber.add_response("get_bucket_cors", {"CORSRules": [existing]}, {"Bucket": BUCKET})
    stubber.add_response(
        "put_bucket_cors",
        {},
        {"Bucket": BUCKET, "CORSConfiguration": {"CORSRules": [existing, _rule(DEV_ORIGINS)]}},
    )
    with stubber:
        assert main() == 0
    stubber.assert_no_pending_responses()
