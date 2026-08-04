"""Validation helpers for write-only Host registration tokens."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def normalized_registration_token(token: str) -> str:
    normalized = token.strip()
    if not normalized or len(normalized) > 4096 or any(char.isspace() for char in normalized):
        raise ValueError("Host 注册 Token 必须是 1 到 4096 个非空白字符")
    return normalized


def registration_token_configured(config: dict[str, Any]) -> bool:
    return Path(str(config["register_token_file"])).is_file()
