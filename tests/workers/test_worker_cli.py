from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import worker.cli as cli
from worker.cli_args import build_parser, configure_payload

ROOT = Path(__file__).resolve().parents[2]


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None, int]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 5,
    ) -> dict[str, Any]:
        self.calls.append((method, path, payload, timeout))
        return self.responses.pop(0)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    responses: list[dict[str, Any]],
) -> tuple[int, FakeClient]:
    client = FakeClient(responses)
    monkeypatch.setattr(sys, "argv", ["workerctl", *arguments])
    monkeypatch.setattr(cli, "resolve_control_token", lambda args: "control-token")
    monkeypatch.setattr(cli, "LocalClient", lambda url, token: client)
    return cli.main(), client


def test_claim_commands_read_and_hot_update_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, client = _run(monkeypatch, ["claim", "status"], [{"claim_enabled": False}])
    assert code == 0
    assert capsys.readouterr().out.strip() == "任务领取: 关闭"
    assert client.calls == [("GET", "/api/status", None, 5)]

    code, client = _run(monkeypatch, ["claim", "enable"], [{"config": {}}])
    assert code == 0
    assert capsys.readouterr().out.strip() == "任务领取: 开启"
    assert client.calls == [("PUT", "/api/config", {"claim_enabled": True}, cli.MUTATE_TIMEOUT)]


def test_capacity_command_reads_and_hot_updates_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, client = _run(monkeypatch, ["capacity", "8"], [{"config": {}}])

    assert code == 0
    assert capsys.readouterr().out.strip() == "动态容量: 8"
    assert client.calls == [("PUT", "/api/config", {"max_concurrency": 8}, cli.MUTATE_TIMEOUT)]


def test_configure_accepts_models_capabilities_and_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "register-token"
    token_file.write_text("host-issued-token\n", encoding="utf-8")
    args = build_parser().parse_args(
        [
            "configure",
            "--capability",
            "review",
            "--model",
            "velites:openai/gpt-5",
            "--register-token-file",
            str(token_file),
        ]
    )

    assert configure_payload(args) == {
        "capabilities": ["review"],
        "models": [{"runtime": "velites", "provider": "openai", "model": "gpt-5"}],
        "register_token": "host-issued-token",
    }


def test_configure_accepts_disable_runtime() -> None:
    args = build_parser().parse_args(
        ["configure", "--disable-runtime", "pi", "--disable-runtime", "openclaw"]
    )

    assert configure_payload(args) == {"disabled_runtimes": ["pi", "openclaw"]}


def test_container_style_standalone_workerctl_can_import_companion_modules(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copyfile(ROOT / "worker/cli.py", bin_dir / "workerctl")
    shutil.copyfile(ROOT / "worker/client.py", bin_dir / "agent_worker_client.py")
    shutil.copyfile(ROOT / "worker/cli_args.py", bin_dir / "agent_worker_cli_args.py")

    result = subprocess.run(
        [sys.executable, str(bin_dir / "workerctl"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "claim" in result.stdout
