from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a transitive dependency
    load_dotenv = None


class AuthError(Exception):
    """Raised when a token cannot be obtained."""


_dotenv_loaded = False


def _load_dotenv_builtin(path: Path) -> None:
    """Minimal .env loader used when python-dotenv is not installed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _maybe_load_dotenv() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    env_path = Path(".env")
    if env_path.exists():
        if load_dotenv is not None:
            load_dotenv(env_path)
        else:
            _load_dotenv_builtin(env_path)
    _dotenv_loaded = True


def _token_gen_config(config: dict[str, Any]) -> dict[str, str]:
    _maybe_load_dotenv()
    cfg = config.get("token_gen") or {}
    return {
        "app_id": str(os.environ.get("BASECMS_APP_ID") or cfg.get("app_id") or ""),
        "nonce": str(os.environ.get("BASECMS_NONCE") or cfg.get("nonce") or ""),
        "secret": str(os.environ.get("BASECMS_SECRET") or cfg.get("secret") or ""),
        "url": str(os.environ.get("BASECMS_TOKEN_URL") or cfg.get("url") or ""),
    }


def _generate_token(config: dict[str, Any]) -> str:
    cfg = _token_gen_config(config)
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise AuthError(
            f"Missing token generation configuration: {', '.join(missing)}. "
            "Provide BASECMS_APP_ID, BASECMS_NONCE, BASECMS_SECRET and BASECMS_TOKEN_URL "
            "via environment variables or config.token_gen."
        )

    timestamp = str(int(time.time()))
    msg = cfg["app_id"] + timestamp + cfg["nonce"]
    sign = hmac.new(cfg["secret"].encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

    payload = {
        "app_id": cfg["app_id"],
        "sign": sign,
        "timestamp": timestamp,
        "nonce": cfg["nonce"],
        "secret": cfg["secret"],
    }
    resp = requests.post(
        cfg["url"],
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    token: str | None = result.get("token")
    if not token:
        data = result.get("data")
        if isinstance(data, dict):
            token = data.get("token")
    if not token:
        raise AuthError(
            f"Token endpoint did not return a token: {json.dumps(result, ensure_ascii=False)}"
        )
    return token


def get_token(config: dict[str, Any]) -> str:
    """Return a Bearer token for the comprehension API.

    Priority:
      1. Environment variable BASECMS_TOKEN.
      2. Direct token configured at config.token.
      3. Generated token using BASECMS_APP_ID / BASECMS_NONCE / BASECMS_SECRET.
    """
    _maybe_load_dotenv()

    direct_token = os.environ.get("BASECMS_TOKEN") or (config.get("token") if config else None)
    if direct_token:
        return str(direct_token)

    return _generate_token(config)
