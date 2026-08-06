"""HTTP client and control-token resolution for the local Worker Service CLI."""

from __future__ import annotations

import argparse
import os
from typing import Any

import requests

READ_TIMEOUT = 5
# restart 会阻塞等待旧进程退出（服务端停止预算约 25s），变更操作给足 60s。
MUTATE_TIMEOUT = 60


class LocalClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int = READ_TIMEOUT,
    ) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.url}{path}",
                json=payload if payload is not None else None,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RuntimeError(_timeout_message(timeout)) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"无法连接本地 Worker Service: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.reason)
            except ValueError:
                detail = response.reason
            raise RuntimeError(f"Worker Service 返回 HTTP {response.status_code}: {detail}")
        return response.json()


def _timeout_message(timeout: int) -> str:
    return f"操作超时（{timeout} 秒），服务端可能已执行，请用 status 确认结果"


def resolve_control_token(args: argparse.Namespace) -> str:
    """优先 --token，其次 AGENT_WORKER_CONTROL_TOKEN，最后读 state dir 下的 control_token。"""
    if args.token:
        return args.token
    if token := os.environ.get("AGENT_WORKER_CONTROL_TOKEN"):
        return token
    path = args.state_dir / "control_token"
    try:
        if token := path.read_text(encoding="utf-8").strip():
            return token
    except OSError:
        pass
    raise RuntimeError(
        f"读不到控制令牌（{path}）；请确认 Worker Service 已启动，"
        "或用 --token / AGENT_WORKER_CONTROL_TOKEN 提供"
    )
