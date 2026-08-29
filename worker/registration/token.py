"""Validation and reading helpers for write-only Host registration tokens."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# scoped token 文件名 = "<token_id>.token"（明文 token 形如 "<id>.<secret>"，
# 取 id 部分命名，控制台展示的尾号即可对应到文件）；不匹配的文件不加载。
TOKEN_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.token$")


def normalized_registration_token(token: str) -> str:
    normalized = token.strip()
    if not normalized or len(normalized) > 4096 or any(char.isspace() for char in normalized):
        raise ValueError("Host 注册 Token 必须是 1 到 4096 个非空白字符")
    return normalized


def validated_registration_token(token: str) -> tuple[str, str]:
    """校验 "<id>.<secret>" 格式并返回 (token_id, normalized)；id 过 TOKEN_FILE_PATTERN
    同款白名单——写入路径由它拼出，未校验的 id（如 "../.."）可路径穿越逃逸目录。"""
    normalized = normalized_registration_token(token)
    token_id, separator, secret = normalized.partition(".")
    if not separator or not TOKEN_FILE_PATTERN.fullmatch(f"{token_id}.token"):
        raise ValueError("Token 格式无效：缺少合法 id 前缀")
    if not secret or "/" in secret or "\\" in secret:
        raise ValueError("Token 格式无效：缺少 secret 部分或含路径分隔符")
    return token_id, normalized


def registration_tokens(config: dict[str, Any], state_dir: Path) -> list[dict[str, Any]]:
    """List configured scoped tokens as [{'token_id', 'token'}] rows.

    Reads the register_token_dir directory (default <state_dir>/register_tokens).
    Legacy single-file setups (register_token_file pointing at one file, e.g.
    the retired /run/secrets/agent_worker_register_token mount) are still
    surfaced as a one-element list so an upgraded worker keeps registering
    until the operator manages tokens in the console."""
    configured_dir = str(config.get("register_token_dir") or "")
    directory = Path(configured_dir) if configured_dir else state_dir / "register_tokens"
    tokens: list[dict[str, Any]] = []
    try:
        files = sorted(directory.iterdir())
    except OSError:
        files = []
    for path in files:
        if not TOKEN_FILE_PATTERN.fullmatch(path.name):
            continue
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            tokens.append({"token_id": path.name[: -len(".token")], "token": token})
    if tokens:
        return tokens
    legacy = config.get("register_token_file")
    if not legacy:
        return []
    try:
        token = Path(str(legacy)).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    return [{"token_id": token.partition(".")[0], "token": token}] if token else []


def registration_token_configured(config: dict[str, Any], state_dir: Path | None = None) -> bool:
    """True when at least one registration credential is readable.

    state_dir is optional for backward compatibility with older callers that
    only probe the legacy single-file field."""
    if state_dir is not None and registration_tokens(config, state_dir):
        return True
    legacy = config.get("register_token_file")
    if not legacy:
        return False
    return Path(str(legacy)).is_file()
