"""Validation for Worker scheduling declarations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_labels(labels: Any) -> dict[str, str]:
    if not isinstance(labels, dict) or len(labels) > 32:
        raise ValueError("标签必须是对象且不能超过 32 项")
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError("标签名必须是 1 到 64 个字符")
        if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 256:
            raise ValueError(f"标签 {key!r} 的值必须是短标量")
        normalized[key] = str(value)
    return normalized


def normalize_capabilities(values: Any) -> list[str]:
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError("Worker 能力必须是最多 128 项的列表")
    capabilities = sorted({str(value).strip() for value in values})
    if any(not value or value == "*" or len(value) > 128 for value in capabilities):
        raise ValueError("Worker 能力必须是 1 到 128 个字符")
    return capabilities


def normalize_models(values: Any, runtimes: Iterable[str]) -> list[dict[str, str]]:
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("模型声明必须是最多 256 项的列表")
    # 第二参是支持全集（catalog.SUPPORTED_RUNTIMES）：生效集合随机器安装
    # 状态浮动，持久化校验不该跟着漂（issue #254）。
    supported = list(runtimes)
    models: set[tuple[str, str, str]] = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("模型声明必须包含 provider 和 model")
        provider = str(item.get("provider", "")).strip()
        model = str(item.get("model", "")).strip()
        declared_runtime = str(item.get("runtime", "")).strip()
        if (
            not provider
            or not model
            or provider == "*"
            or model == "*"
            or len(provider) > 128
            or len(model) > 256
        ):
            raise ValueError("provider/model 必须是非空短字符串")
        targets = [declared_runtime] if declared_runtime else supported
        if any(runtime not in supported for runtime in targets):
            raise ValueError("模型 allowlist 的 runtime 必须是受支持的 runtime")
        models.update((runtime, provider, model) for runtime in targets)
    return [
        {"runtime": runtime, "provider": provider, "model": model}
        for runtime, provider, model in sorted(models)
    ]
