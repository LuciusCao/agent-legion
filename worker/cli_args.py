"""Argument parsing and configuration payloads for workerctl."""

import argparse
from pathlib import Path
from typing import Any


def _labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        key, separator, label_value = value.partition("=")
        if not separator or not key:
            raise ValueError(f"标签必须使用 key=value 格式: {value!r}")
        labels[key] = label_value
    return labels


def _models(values: list[str]) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    for value in values:
        scope, colon, remainder = value.partition(":")
        provider, separator, model = (remainder if colon else value).partition("/")
        provider, model = provider.strip(), model.strip()
        if not separator or not provider or not model:
            raise ValueError(f"模型必须使用 [runtime:]provider/model 格式: {value!r}")
        item = {"provider": provider, "model": model}
        if colon:
            item["runtime"] = scope.strip()
        models.append(item)
    return models


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
    claim = commands.add_parser("claim", help="查看、开启或关闭新任务领取")
    claim.add_argument("action", choices=["status", "enable", "disable"])
    capacity = commands.add_parser("capacity", help="查看或热更新动态容量")
    capacity.add_argument("value", nargs="?", type=int)
    configure = commands.add_parser("configure", help="保存并应用配置（仅更新显式字段）")
    configure.add_argument("--host-url")
    configure.add_argument("--worker-id")
    configure.add_argument("--name")
    configure.add_argument("--runtime", action="append", choices=["pi", "openclaw", "velites"])
    configure.add_argument("--max-concurrency", type=int)
    configure.add_argument("--upload-concurrency", type=int)
    configure.add_argument("--claim-enabled", action=argparse.BooleanOptionalAction, default=None)
    configure.add_argument("--capability", action="append")
    configure.add_argument("--model", action="append", default=[])
    configure.add_argument("--label", action="append", default=[])
    configure.add_argument(
        "--register-token-file",
        type=Path,
        help="从文件读取 Host 签发的注册 token，避免密钥出现在进程参数中",
    )
    return parser


def configure_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for argument, field in (
        ("host_url", "host_url"),
        ("worker_id", "worker_id"),
        ("name", "name"),
        ("runtime", "runtimes"),
        ("max_concurrency", "max_concurrency"),
        ("upload_concurrency", "upload_max_concurrency"),
        ("claim_enabled", "claim_enabled"),
        ("capability", "capabilities"),
    ):
        value = getattr(args, argument)
        if value is not None:
            payload[field] = value
    if args.model:
        payload["models"] = _models(args.model)
    if args.label:
        payload["labels"] = _labels(args.label)
    if args.register_token_file is not None:
        try:
            payload["register_token"] = args.register_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"无法读取注册 token 文件: {exc}") from exc
        if not payload["register_token"]:
            raise ValueError("注册 token 文件不能为空")
    return payload
