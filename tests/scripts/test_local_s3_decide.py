"""Contract tests for scripts/local-s3-decide.sh and the compose profile wiring.

The decision script is pure shell over env files, so tests run it via
subprocess with a sanitized environment (no AGENT_LEGION_* leakage from the
test runner) and fixture env files in tmp_path. Compose/profile wiring is
covered by static checks on the tracked files.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "local-s3-decide.sh"
COMPOSE = ROOT / "deploy" / "compose.host.yaml"

_RUSTFS_ENDPOINT = "http://rustfs:9000"


def _run(
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the decision script with a sanitized env (no ambient AGENT_LEGION_*)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LEGION_")}
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _write_env(tmp_path: Path, content: str, name: str = ".env") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_no_config_starts_local_rustfs(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "missing.env"))
    assert result.returncode == 0
    assert result.stdout.strip() == "start"
    assert "未配置外部 S3" in result.stderr


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:9000",
        "http://localhost:9000",
        "http://[::1]:9000",
        _RUSTFS_ENDPOINT,
        "https://LOCALHOST:9000",
    ],
)
def test_local_endpoints_start(tmp_path: Path, endpoint: str) -> None:
    env_file = _write_env(
        tmp_path,
        f"AGENT_LEGION_S3_ENDPOINT={endpoint}\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run(str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "start"


def test_remote_endpoint_skips(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com\nAGENT_LEGION_S3_BUCKET=b\n",
    )
    result = _run(str(env_file))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"
    assert "外部地址" in result.stderr


def test_bucket_and_keys_without_endpoint_skip(tmp_path: Path) -> None:
    """AWS 写法：bucket+凭据但无 endpoint（boto3 默认端点）→ 跳过。"""
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_BUCKET=b\nAGENT_LEGION_S3_ACCESS_KEY=a\nAGENT_LEGION_S3_SECRET_KEY=c\n",
    )
    result = _run(str(env_file))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"
    assert "默认端点" in result.stderr


def test_bucket_only_without_endpoint_skips(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "AGENT_LEGION_S3_BUCKET=b\n")
    result = _run(str(env_file))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"


def test_default_endpoint_models_compose_injection(tmp_path: Path) -> None:
    """docker stack 形态：compose 给 host 默认注入 rustfs endpoint，零配置
    （凭据已在 deploy/.env）应决策为 start。"""
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ACCESS_KEY=a\nAGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run("--default-endpoint", _RUSTFS_ENDPOINT, str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "start"


def test_start_requires_keys(tmp_path: Path) -> None:
    """已表达本地存储意图（本机 endpoint）但凭据未配齐 → 配置错误。"""
    env_file = _write_env(tmp_path, "AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:9000\n")
    result = _run(str(env_file))
    assert result.returncode == 3
    assert "AGENT_LEGION_S3_ACCESS_KEY" in result.stderr


def test_always_overrides_remote_endpoint(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_LOCAL_S3=always\n"
        "AGENT_LEGION_S3_ENDPOINT=https://minio.example.com\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run(str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "start"


def test_never_skips_without_config(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "AGENT_LEGION_LOCAL_S3=never\n")
    result = _run(str(env_file))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"


def test_invalid_mode_fails_fast(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "AGENT_LEGION_LOCAL_S3=bogus\n")
    result = _run(str(env_file))
    assert result.returncode == 2
    assert "auto|always|never" in result.stderr


def test_process_env_overrides_file(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=https://remote.example.com\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run(
        str(env_file),
        env_extra={"AGENT_LEGION_S3_ENDPOINT": "http://127.0.0.1:9000"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "start"


def test_earlier_env_file_wins(tmp_path: Path) -> None:
    """与 dotenv 一致：同一键先出现的文件生效。"""
    first = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=https://remote.example.com\n",
        name="first.env",
    )
    second = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:9000\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
        name="second.env",
    )
    result = _run(str(first), str(second))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"


def test_quoted_and_exported_values_are_parsed(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path,
        'export AGENT_LEGION_S3_ENDPOINT="https://remote.example.com"\n',
    )
    result = _run(str(env_file))
    assert result.returncode == 0
    assert result.stdout.strip() == "skip"


def test_compose_flags_output(tmp_path: Path) -> None:
    keys = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ACCESS_KEY=a\nAGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    start = _run("--compose-flags", "--default-endpoint", _RUSTFS_ENDPOINT, str(keys))
    assert start.returncode == 0, start.stderr
    assert start.stdout.strip() == "--profile materials-local"

    skip = _run("--compose-flags", env_extra={"AGENT_LEGION_LOCAL_S3": "never"})
    assert skip.returncode == 0
    assert skip.stdout.strip() == ""


# --- 静态接线检查：compose profile 与各 prod-up 入口 ---


def test_compose_rustfs_behind_profile() -> None:
    model = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    rustfs = model["services"]["rustfs"]
    assert "materials-local" in rustfs["profiles"]


def test_compose_host_has_no_hard_dependency_on_rustfs() -> None:
    """depends_on 指向未启用 profile 的服务会让 compose 直接报
    "depends on undefined service"，host 不得再硬依赖 rustfs。"""
    model = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    depends_on = model["services"]["host"].get("depends_on", {})
    assert "rustfs" not in depends_on


def test_compose_host_endpoint_is_overridable() -> None:
    """host 的 endpoint 必须经 deploy/.env 插值（外部 S3 的配置通道），
    且留空可盖掉默认值（AWS 默认端点写法）。"""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${AGENT_LEGION_S3_ENDPOINT-http://rustfs:9000}" in text


def test_prod_up_entries_wire_the_decision_script() -> None:
    native = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    stack = (ROOT / "scripts" / "stack-prod-up.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for name, text in (("native-prod-up.sh", native), ("stack-prod-up.sh", stack)):
        assert "local-s3-decide.sh" in text, name
    assert "--profile materials-local" in stack
    # Makefile 的 stack-host-up 经 --compose-flags 内联同一决策。
    assert "local-s3-decide.sh --compose-flags" in makefile
