"""Static bearer token adapter (platform built-in).

``static_bearer`` treats the stored secret as a ready-made token: no
credential exchange happens, expiry is taken from the JWT ``exp`` claim when
the token is one. Because the token never renews itself, this type suits
long-lived tokens only — anything short-lived belongs behind a real exchange
adapter (``hmac_token``, ``user_login_jwt``), otherwise the cached token dies
on its stated expiry and every refresh returns the same dead value.
"""

from __future__ import annotations

from typing import Any

from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    ConnectionAdapterError,
    bearer_probe,
    jwt_expires_at,
)


def _static_bearer_authenticate(config: dict[str, Any], secrets: dict[str, str]) -> AcquiredToken:
    token = str(secrets.get("token") or "").strip()
    if not token:
        raise ConnectionAdapterError("static_bearer 连接未配置 token")
    return AcquiredToken(token=token, expires_at=jwt_expires_at(token))


def _static_bearer_probe(config: dict[str, Any], secrets: dict[str, str]) -> str:
    acquired = _static_bearer_authenticate(config, secrets)
    return bearer_probe(config, acquired.token)


STATIC_BEARER_ADAPTER = ConnectionAdapter(
    type="static_bearer",
    description="静态 Bearer token（凭据即 token，不过期或按 JWT exp 缓存）",
    required_config_keys=(),
    secret_keys=("token",),
    authenticate=_static_bearer_authenticate,
    probe=_static_bearer_probe,
)
