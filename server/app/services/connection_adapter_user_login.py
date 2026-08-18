"""User-login JWT exchange adapter (platform built-in).

``user_login_jwt`` obtains a component JWT via a two-step user-center login:
first ``POST login_url`` with the app id, account type and the user's
credentials (plus an optional ``client_params`` gate string) to get a
``user_token``; then ``POST auth_url`` with the ``user_token`` and app id to
exchange it for a bearer JWT. Endpoint URLs and app parameters live in the
connection config; the username/password are diverted into the instance
vault. Expiry comes from the returned JWT ``exp`` claim, falling back to a
conservative default for opaque tokens.

The login host may be an internal domain without public DNS: the optional
``login_resolve_ip`` config key dials a fixed IP while preserving the Host
header (like ``curl --resolve``). The session bypasses environment proxies
because internal hosts must not be routed through a local proxy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    ConnectionAdapterError,
    bearer_probe,
    jwt_expires_at,
)

# Opaque tokens (no JWT exp) are re-exchanged after this.
_DEFAULT_TOKEN_TTL = timedelta(minutes=30)


def _resolve_host(url: str, ip: str) -> tuple[str, dict[str, str]]:
    """Rewrite *url* to dial *ip* directly while preserving the Host header."""
    if not ip:
        return url, {}
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    headers = {"Host": f"{host}:{parts.port}" if parts.port else host}
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)), headers


def _failure(step: str, result: Any) -> ConnectionAdapterError:
    """Build an error from the upstream code/message only.

    Never echo the full response body: the request carried the plaintext
    password, and a reflecting endpoint would leak it into API responses and
    job logs (VAULT-SECRET-001).
    """
    code = result.get("code") if isinstance(result, dict) else None
    message = result.get("message") if isinstance(result, dict) else None
    detail = f"code={code}"
    if isinstance(message, str) and message.strip():
        detail += f" message={message[:200]}"
    return ConnectionAdapterError(f"user_login_jwt {step} 失败: {detail}")


def _user_login_authenticate(config: dict[str, Any], secrets: dict[str, str]) -> AcquiredToken:
    app_id = config.get("app_id")
    login_url = str(config.get("login_url") or "").strip()
    auth_url = str(config.get("auth_url") or "").strip()
    uname = str(secrets.get("uname") or "").strip()
    password = str(secrets.get("password") or "").strip()
    missing = [
        name
        for name, value in (
            ("app_id", app_id),
            ("login_url", login_url),
            ("auth_url", auth_url),
            ("uname", uname),
            ("password", password),
        )
        if value is None or (isinstance(value, str) and not value)
    ]
    if missing:
        raise ConnectionAdapterError(f"user_login_jwt 连接缺少配置: {', '.join(missing)}")

    session = requests.Session()
    # Internal login hosts must not be routed through a local HTTP proxy.
    session.trust_env = False

    login_payload: dict[str, Any] = {
        "app_id": app_id,
        "account_type": int(config.get("account_type") or 1),
        "uname": uname,
        "password": password,
    }
    client_params = str(config.get("client_params") or "").strip()
    if client_params:
        login_payload["client_params"] = client_params
    resolve_ip = str(config.get("login_resolve_ip") or "").strip()
    dial_url, dial_headers = _resolve_host(login_url, resolve_ip)
    try:
        resp = session.post(
            dial_url,
            json=login_payload,
            headers={"Content-Type": "application/json", **dial_headers},
            timeout=10,
            # Never follow redirects: the payload carries the plaintext
            # password, and a 307/308 would re-POST it to an unconfigured host.
            allow_redirects=False,
        )
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ConnectionAdapterError(f"user_login_jwt 登录请求失败: {exc}") from exc
    data = result.get("data") if isinstance(result, dict) else None
    user_token = data.get("user_token") if isinstance(data, dict) else None
    if not isinstance(result, dict) or result.get("code") != 200 or not user_token:
        raise _failure("登录", result)

    try:
        resp = session.post(
            auth_url,
            json={"user_token": user_token, "app_id": app_id},
            headers={"Content-Type": "application/json"},
            timeout=10,
            allow_redirects=False,
        )
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ConnectionAdapterError(f"user_login_jwt 换 token 请求失败: {exc}") from exc
    data = result.get("data") if isinstance(result, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(result, dict) or result.get("code") != 0 or not token:
        raise _failure("换 token", result)

    token = str(token)
    expires_at = jwt_expires_at(token)
    if expires_at is None:
        expires_at = datetime.now(UTC) + _DEFAULT_TOKEN_TTL
    return AcquiredToken(token=token, expires_at=expires_at)


def _user_login_probe(config: dict[str, Any], secrets: dict[str, str]) -> str:
    acquired = _user_login_authenticate(config, secrets)
    return bearer_probe(config, acquired.token)


USER_LOGIN_JWT_ADAPTER = ConnectionAdapter(
    type="user_login_jwt",
    description="用户登录两步流换 JWT（login_url 登录得 user_token，auth_url 换 bearer JWT，按 exp 自动续期）",
    required_config_keys=("app_id", "login_url", "auth_url"),
    secret_keys=("uname", "password"),
    authenticate=_user_login_authenticate,
    probe=_user_login_probe,
)
