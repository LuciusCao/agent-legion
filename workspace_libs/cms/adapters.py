"""CMS connection adapters (question/video knowledge workspace pack).

Auth protocol semantics for the CMS live here, outside the platform:
``cms_hmac`` signs ``app_id + timestamp + nonce`` with the instance-vault
secret and exchanges it for a JWT at the CMS token endpoint. The platform
only knows the adapter protocol (server.app.services.connection_adapters).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests

from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    ConnectionAdapterError,
    bearer_probe,
    jwt_expires_at,
)


def _cms_hmac_authenticate(config: dict[str, Any], secrets: dict[str, str]) -> AcquiredToken:
    app_id = str(config.get("app_id") or "").strip()
    nonce = str(config.get("nonce") or "").strip()
    token_url = str(config.get("token_url") or "").strip()
    secret = str(secrets.get("secret") or "").strip()
    missing = [
        name
        for name, value in (
            ("app_id", app_id),
            ("nonce", nonce),
            ("token_url", token_url),
            ("secret", secret),
        )
        if not value
    ]
    if missing:
        raise ConnectionAdapterError(f"cms_hmac 连接缺少配置: {', '.join(missing)}")
    timestamp = str(int(time.time()))
    msg = app_id + timestamp + nonce
    sign = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = {
        "app_id": app_id,
        "sign": sign,
        "timestamp": timestamp,
        "nonce": nonce,
        "secret": secret,
    }
    try:
        resp = requests.post(
            token_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10
        )
        status = resp.status_code
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ConnectionAdapterError(f"CMS token request failed: {exc}") from exc
    token = result.get("token") if isinstance(result, dict) else None
    if not token and isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            token = data.get("token")
    if not token:
        # Never echo the response body: the request carried the plaintext
        # secret, and a reflecting endpoint would leak it into API responses
        # and job logs (VAULT-SECRET-001).
        raise ConnectionAdapterError(f"生成 token 失败:响应缺少 token 字段 (HTTP {status})")
    return AcquiredToken(token=str(token), expires_at=jwt_expires_at(str(token)))


def _cms_hmac_probe(config: dict[str, Any], secrets: dict[str, str]) -> str:
    acquired = _cms_hmac_authenticate(config, secrets)
    return bearer_probe(config, acquired.token)


CMS_HMAC_ADAPTER = ConnectionAdapter(
    type="cms_hmac",
    description="CMS HMAC 签名换 token（app_id/nonce + secret 签名 POST 换 JWT）",
    required_config_keys=("app_id", "nonce", "token_url"),
    secret_keys=("secret",),
    authenticate=_cms_hmac_authenticate,
    probe=_cms_hmac_probe,
)
