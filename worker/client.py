"""HTTP client and control-token resolution for the local Worker Service CLI."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from typing import Any

READ_TIMEOUT = 5
# restart 会阻塞等待旧进程退出（服务端停止预算约 25s），变更操作给足 60s。
MUTATE_TIMEOUT = 60


class LocalClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int = READ_TIMEOUT,
    ) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {self.token}"}
        if data:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.url}{path}", method=method, data=data, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", exc.reason)
            except (ValueError, AttributeError):
                detail = exc.reason
            raise RuntimeError(f"Worker Service 返回 HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise RuntimeError(_timeout_message(timeout)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise RuntimeError(_timeout_message(timeout)) from exc
            raise RuntimeError(f"无法连接本地 Worker Service: {exc.reason}") from exc


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
