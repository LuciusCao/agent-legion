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
_SEAWEEDFS_ENDPOINT = "http://seaweedfs:8333"


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
        _SEAWEEDFS_ENDPOINT,
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


def test_explicit_empty_endpoint_skips_docker_entry(tmp_path: Path) -> None:
    """docker 入口：显式空 endpoint = AWS 默认端点（与 compose ${VAR-default}
    语义一致），不得被 --default-endpoint 兜底误判为本机 RustFS（无凭据时
    原先会 rc 3 阻断启动）。"""
    env_file = _write_env(tmp_path, "AGENT_LEGION_S3_ENDPOINT=\n")
    result = _run("--default-endpoint", _RUSTFS_ENDPOINT, str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"
    assert "显式置空" in result.stderr


def test_explicit_empty_endpoint_with_bucket_skips(tmp_path: Path) -> None:
    """显式空 endpoint + 配了 bucket：同样按外部对象存储 skip。"""
    env_file = _write_env(tmp_path, "AGENT_LEGION_S3_ENDPOINT=\nAGENT_LEGION_S3_BUCKET=b\n")
    result = _run("--default-endpoint", _RUSTFS_ENDPOINT, str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"


def test_explicit_empty_endpoint_skips_native_entry(tmp_path: Path) -> None:
    """native-prod-up.sh 不传 --default-endpoint：显式空值同样 skip，
    而不是回落「未配置外部 S3 → start」。"""
    env_file = _write_env(tmp_path, "AGENT_LEGION_S3_ENDPOINT=\n")
    result = _run(str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"


def test_explicit_empty_endpoint_from_process_env_skips(tmp_path: Path) -> None:
    """进程环境里 set-but-empty 同样算「出现」。"""
    result = _run(
        "--default-endpoint",
        _RUSTFS_ENDPOINT,
        str(tmp_path / "missing.env"),
        env_extra={"AGENT_LEGION_S3_ENDPOINT": ""},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"


def test_process_env_empty_endpoint_overrides_local_file_value(tmp_path: Path) -> None:
    """进程显式空 endpoint（=AWS 默认端点）优先于文件里的本机地址：
    首次出现即生效，空值也是值，不得回退到文件取值。"""
    env_file = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:9000\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run(str(env_file), env_extra={"AGENT_LEGION_S3_ENDPOINT": ""})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"
    assert "显式置空" in result.stderr


def test_earlier_file_empty_endpoint_wins_over_later_local(tmp_path: Path) -> None:
    """文件 A 显式空 + 文件 B 本机地址：A 先出现即生效 → skip。"""
    first = _write_env(tmp_path, "AGENT_LEGION_S3_ENDPOINT=\n", name="first.env")
    second = _write_env(
        tmp_path,
        "AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:9000\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
        name="second.env",
    )
    result = _run(str(first), str(second))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "skip"


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
    # 默认后端 seaweedfs：占既有 profile 名 materials-local。
    start = _run("--compose-flags", "--default-endpoint", _SEAWEEDFS_ENDPOINT, str(keys))
    assert start.returncode == 0, start.stderr
    assert start.stdout.strip() == "--profile materials-local"

    # 显式 rustfs：分派到逃生舱 profile。
    start_rustfs = _run(
        "--compose-flags",
        "--default-endpoint",
        _SEAWEEDFS_ENDPOINT,
        str(keys),
        env_extra={"AGENT_LEGION_LOCAL_S3_BACKEND": "rustfs"},
    )
    assert start_rustfs.returncode == 0, start_rustfs.stderr
    assert start_rustfs.stdout.strip() == "--profile materials-local-rustfs"

    skip = _run("--compose-flags", env_extra={"AGENT_LEGION_LOCAL_S3": "never"})
    assert skip.returncode == 0
    assert skip.stdout.strip() == ""


def test_invalid_backend_fails_fast(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "AGENT_LEGION_LOCAL_S3_BACKEND=minio\n")
    result = _run(str(env_file))
    assert result.returncode == 2
    assert "seaweedfs|rustfs" in result.stderr


def test_service_name_dispatches_backend(tmp_path: Path) -> None:
    """--service-name：输出所选后端的 compose 服务名，供 up -d <服务名>。"""
    default = _run("--service-name")
    assert default.returncode == 0, default.stderr
    assert default.stdout.strip() == "seaweedfs"

    rustfs = _run("--service-name", env_extra={"AGENT_LEGION_LOCAL_S3_BACKEND": "rustfs"})
    assert rustfs.returncode == 0, rustfs.stderr
    assert rustfs.stdout.strip() == "rustfs"


def test_backend_mismatch_with_rustfs_endpoint_warns(tmp_path: Path) -> None:
    """存量用户 endpoint 指向 rustfs 而默认 backend 已是 seaweedfs：给迁移
    指引提示（不阻断决策）。"""
    env_file = _write_env(
        tmp_path,
        f"AGENT_LEGION_S3_ENDPOINT={_RUSTFS_ENDPOINT}\n"
        "AGENT_LEGION_S3_ACCESS_KEY=a\n"
        "AGENT_LEGION_S3_SECRET_KEY=b\n",
    )
    result = _run(str(env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "start"
    assert "AGENT_LEGION_LOCAL_S3_BACKEND=rustfs" in result.stderr


# --- 静态接线检查：compose 双后端与各 prod-up 入口 ---


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_compose_seaweedfs_is_default_backend() -> None:
    """seaweedfs 占既有 profile 名 materials-local（默认后端，#340）；
    镜像 pin 明确版本而非 latest（行为漂移防护，同 rustfs 逃生舱的 pin
    理由）。"""
    seaweedfs = _services()["seaweedfs"]
    assert seaweedfs["profiles"] == ["materials-local"]
    assert seaweedfs["image"].startswith("chrislusf/seaweedfs:")
    assert seaweedfs["image"] != "chrislusf/seaweedfs:latest"


def test_compose_rustfs_is_escape_hatch_backend() -> None:
    """rustfs 挪到独立 profile（AGENT_LEGION_LOCAL_S3_BACKEND=rustfs 的
    逃生舱）；#340 的修复必须在位：镜像 pin、scanner/heal 显式关闭。"""
    rustfs = _services()["rustfs"]
    assert rustfs["profiles"] == ["materials-local-rustfs"]
    assert rustfs["image"] == "rustfs/rustfs:1.0.0-beta.12"
    env = rustfs["environment"]
    assert env.get("RUSTFS_SCANNER_ENABLED") == "false"
    assert env.get("RUSTFS_HEAL_ENABLED") == "false"


def test_compose_healthchecks_probe_real_liveness_endpoints() -> None:
    """健康探针不得退回根路径（#340 原始事故：S3 API 对匿名请求返回
    403，探 / 会让容器永远 unhealthy）。seaweedfs 探 weed 的 /healthz，
    rustfs 探 MinIO 兼容 /minio/health/live——revert 回根路径时此测试
    必须红（系统性评审 #346：此前无任何测试钉住探针端点）。"""
    services = _services()
    seaweedfs_test = str(services["seaweedfs"]["healthcheck"]["test"])
    rustfs_test = str(services["rustfs"]["healthcheck"]["test"])
    assert "/healthz" in seaweedfs_test
    assert "/minio/health/live" in rustfs_test
    # 根路径探针形态（URL 端口后紧跟路径终点）两种后端都不允许出现。
    assert "8333/ " not in seaweedfs_test and "8333/'" not in seaweedfs_test
    assert "9000/ " not in rustfs_test and "9000/'" not in rustfs_test


def test_compose_seaweedfs_s3_config_readable_after_privilege_drop() -> None:
    """umask 077（s3.json 收紧到 0600）与 su-exec 降权并存时，文件属主必须
    先对齐运行用户：root 名下的 0600 在降权后不可读，weed 加载 s3.config 即
    glog.Fatalf，容器陷入重启循环（#340 部署线；引入点是 #346 评审提交
    5c5a8932 加 umask 077 时没配套 chown，表象是 raft "Not current leader"，
    实为容器活不过选主完成）。revert 掉 chown、或把它挪到 exec su-exec
    之后（exec 后不再返回，永远不会执行）时此测试必须红。"""
    command = str(_services()["seaweedfs"]["command"])
    assert "umask 077" in command
    assert "-s3.config=/tmp/s3.json" in command
    assert "chown seaweed:seaweed /tmp/s3.json" in command
    # 顺序契约：chown 必须发生在 exec su-exec 降权之前。
    assert command.index("chown seaweed:seaweed /tmp/s3.json") < command.index("exec su-exec")


def test_compose_host_has_no_hard_dependency_on_local_backends() -> None:
    """depends_on 指向未启用 profile 的服务会让 compose 直接报
    "depends on undefined service"，host 不得硬依赖任一本地后端。"""
    depends_on = _services()["host"].get("depends_on", {})
    assert "rustfs" not in depends_on
    assert "seaweedfs" not in depends_on


def test_compose_host_endpoint_is_overridable() -> None:
    """host 的 endpoint 必须经 deploy/.env 插值（外部 S3 的配置通道），
    且留空可盖掉默认值（AWS 默认端点写法）；默认值与默认后端
    seaweedfs 的 compose 内部地址一致。"""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${AGENT_LEGION_S3_ENDPOINT-http://seaweedfs:8333}" in text


def test_prod_up_entries_wire_the_decision_script() -> None:
    native = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    stack = (ROOT / "scripts" / "stack-prod-up.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for name, text in (("native-prod-up.sh", native), ("stack-prod-up.sh", stack)):
        assert "local-s3-decide.sh" in text, name
    # native 入口经 --service-name 取所选后端的服务名（双后端分派）。
    assert "local-s3-decide.sh --service-name" in native
    # stack 入口的 profile 由 decide 脚本 --compose-flags 分派（不再写死）。
    assert "--compose-flags" in stack
    assert "default-endpoint http://seaweedfs:8333" in stack
    # Makefile 的 stack-host-up 经 --compose-flags 内联同一决策。
    assert "local-s3-decide.sh --compose-flags" in makefile
    assert "default-endpoint http://seaweedfs:8333" in makefile
