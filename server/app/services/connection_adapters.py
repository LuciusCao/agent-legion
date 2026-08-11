"""External connection type adapters: platform protocol + registry.

The platform owns the connection *mechanism* (storage, admin API, token
caching); auth-protocol semantics live with the workspace integration packs
under ``workspace_libs/``. The registry maps a type string to a lazy import
location so ``server/app`` itself carries no vendor-specific knowledge — a
new external service type is added by dropping an adapter into a workspace
pack and registering its import path here.

Only the trivial ``static_bearer`` type (a ready-made token) is implemented
in the platform; everything with real auth semantics lives outside it.
"""

from __future__ import annotations

import base64
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests


class ConnectionAdapterError(RuntimeError):
    """Adapter-level failure (bad config, upstream unreachable, bad response)."""


@dataclass(frozen=True)
class AcquiredToken:
    token: str
    # None = no known expiry; the cache entry lives until an auth failure is
    # reported or the connection is reconfigured.
    expires_at: datetime | None


@dataclass(frozen=True)
class ConnectionAdapter:
    """Auth protocol implementation for one connection type.

    ``authenticate`` performs the (possibly multi-step) credential exchange
    and returns a token plus its expiry; it must use bounded network timeouts
    because the token service holds the single-flight row lock across the
    call. ``probe`` is the admin-side
    connection test: it must raise :class:`ConnectionAdapterError` on failure
    and return a human-readable success message otherwise.
    """

    type: str
    description: str
    required_config_keys: tuple[str, ...]
    secret_keys: tuple[str, ...]
    authenticate: Callable[[dict[str, Any], dict[str, str]], AcquiredToken]
    probe: Callable[[dict[str, Any], dict[str, str]], str]


def jwt_expires_at(token: str) -> datetime | None:
    """Best-effort ``exp`` claim parse (no signature verification)."""
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment.encode("utf-8")))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)) or exp <= 0:
        return None
    return datetime.fromtimestamp(float(exp), tz=UTC)


def bearer_probe(config: dict[str, Any], token: str) -> str:
    """Shared probe: GET ``probe_url`` with the token, 401/403 means bad auth."""
    probe_url = str(config.get("probe_url") or "").strip()
    if not probe_url:
        return "token 获取成功（未配置 probe_url，跳过连通性探测）"
    try:
        resp = requests.get(
            probe_url,
            headers={"Accept": "*/*", "Authorization": f"Bearer {token}"},
            timeout=10,
            # Do not follow redirects: a redirect target is not the configured
            # endpoint (and cross-host redirects would drop/leak the bearer).
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ConnectionAdapterError(f"无法连接: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ConnectionAdapterError(f"服务可达但鉴权失败 (HTTP {resp.status_code})")
    if 200 <= resp.status_code < 300:
        return f"连接成功 (HTTP {resp.status_code})"
    # Anything else (5xx, 404, 3xx, …) means the endpoint answered but is not
    # serving the probed resource correctly — reachable, not "connected".
    raise ConnectionAdapterError(f"服务可达但端点响应异常 (HTTP {resp.status_code})")


def _static_bearer_authenticate(config: dict[str, Any], secrets: dict[str, str]) -> AcquiredToken:
    token = str(secrets.get("token") or "").strip()
    if not token:
        raise ConnectionAdapterError("static_bearer 连接未配置 token")
    return AcquiredToken(token=token, expires_at=jwt_expires_at(token))


def _static_bearer_probe(config: dict[str, Any], secrets: dict[str, str]) -> str:
    acquired = _static_bearer_authenticate(config, secrets)
    return bearer_probe(config, acquired.token)


_STATIC_BEARER = ConnectionAdapter(
    type="static_bearer",
    description="静态 Bearer token（凭据即 token，不过期或按 JWT exp 缓存）",
    required_config_keys=(),
    secret_keys=("token",),
    authenticate=_static_bearer_authenticate,
    probe=_static_bearer_probe,
)

# type → adapter, or (module, attribute) for lazy loading from workspace packs.
_REGISTRY: dict[str, ConnectionAdapter | tuple[str, str]] = {
    "static_bearer": _STATIC_BEARER,
    "cms_hmac": ("workspace_libs.cms.adapters", "CMS_HMAC_ADAPTER"),
}


def get_adapter(type_name: str) -> ConnectionAdapter:
    entry = _REGISTRY.get(type_name)
    if entry is None:
        raise ConnectionAdapterError(f"unknown connection type {type_name!r}")
    if isinstance(entry, ConnectionAdapter):
        return entry
    module_name, attribute = entry
    try:
        adapter = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ConnectionAdapterError(
            f"connection type {type_name!r} 的实现加载失败: {exc}"
        ) from exc
    if not isinstance(adapter, ConnectionAdapter):
        raise ConnectionAdapterError(f"invalid adapter for connection type {type_name!r}")
    _REGISTRY[type_name] = adapter
    return adapter


def list_adapter_types() -> list[dict[str, Any]]:
    """Type metadata for the admin UI (never includes secret values)."""
    return [
        {
            "type": adapter.type,
            "description": adapter.description,
            "required_config_keys": list(adapter.required_config_keys),
            "secret_keys": list(adapter.secret_keys),
        }
        for adapter in (get_adapter(type_name) for type_name in sorted(_REGISTRY))
    ]
