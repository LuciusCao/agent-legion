#!/usr/bin/env python3
"""CLI for querying and configuring a local Agent Worker Service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from agent_worker_client import MUTATE_TIMEOUT, LocalClient, resolve_control_token
except ModuleNotFoundError:  # 作为 worker 包的一部分被导入时（如测试、python -m worker.cli）
    from worker.client import MUTATE_TIMEOUT, LocalClient, resolve_control_token


def _labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        key, separator, label_value = value.partition("=")
        if not separator or not key:
            raise ValueError(f"标签必须使用 key=value 格式: {value!r}")
        labels[key] = label_value
    return labels


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workerctl", description="管理本机 Agent Worker")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--token", help="控制令牌，默认读 state dir 或 AGENT_WORKER_CONTROL_TOKEN")
    parser.add_argument("--state-dir", type=Path, default=Path("data/agent-worker-service"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="查看进程、Host 和登记状态")
    commands.add_parser("config", help="查看可编辑配置")
    commands.add_parser("restart", help="重启 Worker 执行进程")
    logs = commands.add_parser("logs", help="查看最近日志")
    logs.add_argument("--limit", type=int, default=100)
    configure = commands.add_parser("configure", help="保存并应用配置（仅更新显式传入的字段）")
    configure.add_argument("--host-url")
    configure.add_argument("--worker-id")
    configure.add_argument("--name")
    configure.add_argument("--runtime", action="append", choices=["pi", "openclaw"])
    configure.add_argument("--max-concurrency", type=int)
    configure.add_argument("--claim-enabled", action=argparse.BooleanOptionalAction, default=None)
    configure.add_argument("--capability", action="append")
    configure.add_argument("--label", action="append", default=[])
    return parser


def _configure_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for argument, field in (
        ("host_url", "host_url"),
        ("worker_id", "worker_id"),
        ("name", "name"),
        ("runtime", "runtimes"),
        ("max_concurrency", "max_concurrency"),
        ("claim_enabled", "claim_enabled"),
        ("capability", "capabilities"),
    ):
        value = getattr(args, argument)
        if value is not None:
            payload[field] = value
    if args.label:
        payload["labels"] = _labels(args.label)
    return payload


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
        else:
            result = client.request(
                "PUT", "/api/config", _configure_payload(args), timeout=MUTATE_TIMEOUT
            )
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
