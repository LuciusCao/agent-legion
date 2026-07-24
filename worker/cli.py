#!/usr/bin/env python3
"""CLI for querying and configuring a local Agent Worker Service."""

from __future__ import annotations

import argparse
import json
from typing import Any

try:
    from agent_worker_cli_args import build_parser, configure_payload
    from agent_worker_client import MUTATE_TIMEOUT, LocalClient, resolve_control_token
except ModuleNotFoundError:  # 作为 worker 包的一部分被导入时（如测试、python -m worker.cli）
    from worker.cli_args import build_parser, configure_payload
    from worker.client import MUTATE_TIMEOUT, LocalClient, resolve_control_token


def _print_status(status: dict[str, Any]) -> None:
    state = "已登记" if status.get("connected") else "未登记"
    print(f"Worker: {state}")
    print(f"服务: {status.get('service', 'unknown')}")
    print(f"执行进程: {'运行中' if status.get('worker_running') else '未运行'}")
    print(f"任务领取: {'开启' if status.get('claim_enabled') else '关闭'}")
    print(f"动态容量: {status.get('max_concurrency', 'unknown')}")
    print(f"Host 可达: {'是' if status.get('host_reachable') else '否'}")
    if failed := status.get("failed"):
        print(f"故障: {failed}")
    worker = status.get("host_worker") or {}
    if worker:
        print(f"Host 登记: {worker.get('worker_id')} / {worker.get('name')}")
        scope = worker.get("allowed_workspaces", [])
        print(f"允许工作区: {', '.join(scope) if scope else '全部'}")
        print(f"最后在线: {worker.get('last_seen_at')}")
    if error := status.get("connection_error"):
        print(f"连接错误: {error}")


def _print_value(
    args: argparse.Namespace, key: str, value: Any, label: str, display: Any | None = None
) -> None:
    output = value if display is None else display
    print(
        json.dumps({key: value}, ensure_ascii=False, indent=2)
        if args.as_json
        else f"{label}: {output}"
    )


def _update(client: LocalClient, payload: dict[str, Any]) -> dict[str, Any]:
    return client.request("PUT", "/api/config", payload, timeout=MUTATE_TIMEOUT)


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = LocalClient(args.url, resolve_control_token(args))
        if args.command == "status":
            result = client.request("GET", "/api/status")
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_status(result)
        elif args.command == "config":
            print(json.dumps(client.request("GET", "/api/config"), ensure_ascii=False, indent=2))
        elif args.command == "restart":
            result = client.request("POST", "/api/restart", timeout=MUTATE_TIMEOUT)
            print(
                json.dumps(result, ensure_ascii=False, indent=2)
                if args.as_json
                else "已重启（新配置已生效）"
            )
        elif args.command == "logs":
            result = client.request("GET", f"/api/logs?limit={args.limit}")
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("\n".join(result["lines"]))
        elif args.command == "claim":
            if args.action == "status":
                enabled = bool(client.request("GET", "/api/status").get("claim_enabled"))
            else:
                enabled = args.action == "enable"
                _update(client, {"claim_enabled": enabled})
            _print_value(args, "claim_enabled", enabled, "任务领取", "开启" if enabled else "关闭")
        elif args.command == "capacity":
            if args.value is None:
                capacity = client.request("GET", "/api/status").get("max_concurrency")
            else:
                capacity = args.value
                _update(client, {"max_concurrency": capacity})
            _print_value(args, "max_concurrency", capacity, "动态容量")
        else:
            result = _update(client, configure_payload(args))
            print(
                json.dumps(result, ensure_ascii=False, indent=2)
                if args.as_json
                else "配置已保存并生效"
            )
    except (RuntimeError, ValueError) as exc:
        print(f"错误: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
