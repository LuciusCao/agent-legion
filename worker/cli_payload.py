"""Configuration payload conversion for workerctl."""

import argparse
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


def configure_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for argument, field in (
        ("host_url", "host_url"),
        ("worker_id", "worker_id"),
        ("name", "name"),
        ("disable_runtime", "disabled_runtimes"),
        ("max_concurrency", "max_concurrency"),
        ("upload_concurrency", "upload_max_concurrency"),
        ("claim_enabled", "claim_enabled"),
        ("proxy", "proxy"),
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
