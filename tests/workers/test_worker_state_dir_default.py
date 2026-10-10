"""#1106：workerctl 与 worker.service 的 --state-dir 默认值读 AGENT_WORKER_STATE_DIR。

容器内 WORKDIR 为 /app、状态卷在 /var/lib/agent-legion-worker-control，CLI 默认
相对路径 data/agent-worker-service 读不到 control_token；镜像经 Dockerfile ENV
设置该变量，CLI 与 service 共用同一默认值来源（worker/cli_args.default_state_dir）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from worker import service
from worker.cli_args import LOCAL_STATE_DIR, STATE_DIR_ENV, build_parser
from worker.client import resolve_control_token

ROOT = Path(__file__).resolve().parents[2]
CONTAINER_STATE_DIR = "/var/lib/agent-legion-worker-control"

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, Path(LOCAL_STATE_DIR)),
        ("", Path(LOCAL_STATE_DIR)),
        (CONTAINER_STATE_DIR, Path(CONTAINER_STATE_DIR)),
    ],
)
def test_cli_state_dir_default_reads_env_at_parse_time(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: Path
) -> None:
    if env_value is None:
        monkeypatch.delenv(STATE_DIR_ENV, raising=False)
    else:
        monkeypatch.setenv(STATE_DIR_ENV, env_value)
    assert build_parser().parse_args(["status"]).state_dir == expected


def test_cli_explicit_state_dir_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STATE_DIR_ENV, CONTAINER_STATE_DIR)
    args = build_parser().parse_args(["--state-dir", "/explicit", "status"])
    assert args.state_dir == Path("/explicit")


def test_control_token_resolved_from_env_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "control_token").write_text("from-env-dir\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_WORKER_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.chdir(tmp_path)  # 相对默认路径在此处不存在，命中只能来自环境变量
    assert resolve_control_token(build_parser().parse_args(["status"])) == "from-env-dir"


def test_service_state_dir_default_shares_cli_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path] = []

    class _FakeStore:
        def __init__(self, state_dir: Path, *_args: object) -> None:
            seen.append(state_dir)

    monkeypatch.setattr(service, "WorkerConfigStore", _FakeStore)
    monkeypatch.setattr(service, "WorkerSupervisor", lambda *a, **k: None)
    monkeypatch.setattr(service, "create_app", lambda *a, **k: None)
    monkeypatch.setattr(service.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(service, "strip_proxy_env", lambda: None)
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "svc-state"))
    monkeypatch.setattr("sys.argv", ["worker.service"])

    service.main()

    assert seen == [(tmp_path / "svc-state").resolve()]


@pytest.fixture
def fake_worker_service() -> Iterator[tuple[str, list[str]]]:
    """最小 /api/status 端点，记录收到的 Authorization 头。"""
    auth_headers: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server 接口命名
            auth_headers.append(self.headers.get("Authorization", ""))
            body = json.dumps({"service": "running", "connected": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", auth_headers
    finally:
        server.shutdown()
        server.server_close()


def test_container_style_workerctl_status_needs_no_state_dir_flag(
    tmp_path: Path, fake_worker_service: tuple[str, list[str]]
) -> None:
    """镜像布局复刻：/usr/local/bin 单文件 + cwd 下无 data/，仅靠 ENV 读到令牌。"""
    url, auth_headers = fake_worker_service
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copyfile(ROOT / "worker/cli.py", bin_dir / "workerctl")
    shutil.copyfile(ROOT / "worker/client.py", bin_dir / "agent_worker_client.py")
    shutil.copyfile(ROOT / "worker/cli_args.py", bin_dir / "agent_worker_cli_args.py")
    state = tmp_path / "state"
    state.mkdir()
    (state / "control_token").write_text("ctl-token\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "AGENT_WORKER_CONTROL_TOKEN"}
    env[STATE_DIR_ENV] = str(state)

    result = subprocess.run(
        [sys.executable, str(bin_dir / "workerctl"), "--url", url, "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Worker: 已登记" in result.stdout
    assert auth_headers == ["Bearer ctl-token"]


def test_dockerfile_env_is_the_only_state_dir_source_and_matches_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ENV {STATE_DIR_ENV}={CONTAINER_STATE_DIR}" in dockerfile
    cmd = re.search(r'^CMD \[.*/worker\.yaml".*\]$', dockerfile, re.MULTILINE)
    assert cmd is not None
    # CMD 不再重复声明 --state-dir：ENV 是镜像内 service 与 workerctl 的唯一来源
    assert "--state-dir" not in cmd.group(0)
    for name in (
        "deploy/compose.worker.yaml",
        "deploy/compose.worker.standalone.yaml",
        "deploy/compose.host.yaml",
    ):
        worker = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]["worker"]
        assert f"worker-control:{CONTAINER_STATE_DIR}" in worker["volumes"], name
        # compose 不得覆盖 command / 环境变量，否则会与镜像 ENV 分叉
        assert "command" not in worker, name
        assert STATE_DIR_ENV not in str(worker.get("environment", "")), name
