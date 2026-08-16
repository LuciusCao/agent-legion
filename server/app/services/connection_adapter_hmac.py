"""Generic HMAC-signed token exchange adapter (platform built-in).

``hmac_token`` signs ``app_id + timestamp + nonce`` with the connection
secret (HMAC-SHA256, hex digest) and POSTs the signature to the configured
token endpoint in exchange for a bearer token (typically a JWT). The
protocol is vendor-neutral: endpoint URLs and the app id live in the
connection config, the signing secret in the instance vault. Expiry is
taken from the returned JWT ``exp`` claim when present, then from a
numeric ``expires_in`` (seconds) in the response, falling back to a
conservative default so an opaque token is re-exchanged periodically
instead of being cached until an upstream auth failure.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from typing import Any

import requests

from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    ConnectionAdapterError,
    bearer_probe,
    jwt_expires_at,
)

# Opaque tokens (no JWT exp, no expires_in) are re-exchanged after this.
_DEFAULT_TOKEN_TTL = timedelta(minutes=30)


def _expires_in_seconds(result: dict[str, Any]) -> float | None:
    containers: list[dict[str, Any]] = [result]
    data = result.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        value = container.get("expires_in")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                continue
            if parsed > 0:
                return parsed
    return None


def _hmac_token_authenticate(config: dict[str, Any], secrets: dict[str, str]) -> AcquiredToken:
    app_id = str(config.get("app_id") or "").strip()
    token_url = str(config.get("token_url") or "").strip()
    secret = str(secrets.get("secret") or "").strip()
    missing = [
        name
        for name, value in (("app_id", app_id), ("token_url", token_url), ("secret", secret))
        if not value
    ]
    if missing:
        raise ConnectionAdapterError(f"hmac_token 连接缺少配置: {', '.join(missing)}")
    # A per-request random nonce is the default; a fixed ``nonce`` config key
    # overrides it for endpoints that pin the nonce.
    nonce = str(config.get("nonce") or "").strip() or token_hex(16)
    timestamp = str(int(time.time()))
    msg = app_id + timestamp + nonce
    sign = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    # The endpoint contract authenticates with the signature *and* the
    # plaintext secret over HTTPS; never echo the response body on failure —
    # a reflecting endpoint would leak the secret into API responses and job
    # logs (VAULT-SECRET-001).
    payload = {
        "app_id": app_id,
        "sign": sign,
        "timestamp": timestamp,
        "nonce": nonce,
        "secret": secret,
    }
    try:
        resp = requests.post(
            token_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
            # Do not follow redirects: the payload carries the plaintext
            # secret, and a 307/308 redirect would re-POST it to a host the
            # admin never configured (same rule as bearer_probe).
            allow_redirects=False,
        )
        status = resp.status_code
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ConnectionAdapterError(f"token 请求失败: {exc}") from exc
    token = result.get("token") if isinstance(result, dict) else None
    if not token and isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            token = data.get("token")
    if not token:
        raise ConnectionAdapterError(f"生成 token 失败:响应缺少 token 字段 (HTTP {status})")
    token = str(token)
    expires_at = jwt_expires_at(token)
    if expires_at is None and isinstance(result, dict):
        expires_in = _expires_in_seconds(result)
        if expires_in is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    if expires_at is None:
        expires_at = datetime.now(UTC) + _DEFAULT_TOKEN_TTL
    return AcquiredToken(token=token, expires_at=expires_at)


def _hmac_token_probe(config: dict[str, Any], secrets: dict[str, str]) -> str:
    acquired = _hmac_token_authenticate(config, secrets)
    return bearer_probe(config, acquired.token)


HMAC_TOKEN_ADAPTER = ConnectionAdapter(
    type="hmac_token",
    description="HMAC 签名换 token（app_id + timestamp + nonce 用 secret 签名，POST 换 bearer token）",
    required_config_keys=("app_id", "token_url"),
    secret_keys=("secret",),
    authenticate=_hmac_token_authenticate,
    probe=_hmac_token_probe,
)
