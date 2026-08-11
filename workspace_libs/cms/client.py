"""CMS HTTP client primitives (question/video knowledge workspace pack).

Token acquisition is the connection layer's job: by dispatch time the token
is already resolved into the node config in memory (instance-level external
connection, see ``server.app.services.connection_tokens``). This module only
knows how to talk to the CMS with a ready token.
"""

from dataclasses import dataclass
from typing import Any

import requests


class CmsClientError(RuntimeError):
    pass


def require_api_url(api_url: str | None, resource: str) -> str:
    """Return the configured CMS URL or fail with configuration guidance.

    There is no built-in fallback host: the URL comes from the external
    connection config (admin settings) or a node/workspace override.
    """
    url = str(api_url or "").strip()
    if url:
        return url
    raise CmsClientError(
        f"CMS {resource} URL is not configured: set base_url/api_url on the "
        "external connection (admin settings → 外部服务连接)"
    )


def _build_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "*/*"}
    if token:
        token = token.strip()
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    return headers


def _fetch_json(url: str, params: dict[str, Any], token: str | None, timeout: int = 15) -> dict:
    try:
        resp = requests.get(url, params=params, headers=_build_headers(token), timeout=timeout)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
    except (requests.RequestException, ValueError) as exc:
        raise CmsClientError(f"CMS request failed: {exc}") from exc


@dataclass(frozen=True)
class CmsVideoLookup:
    status: str
    url: str = ""
    title: str = ""
    source_uuid: str = ""
    payload: dict[str, Any] | None = None


def get_token(env: str, config: dict[str, Any] | None = None) -> str | None:
    """Return the dispatch-resolved token from the config, if present.

    The ``env`` parameter is kept for call-site compatibility and ignored:
    token generation/caching moved to the instance-level connection layer;
    legacy frozen payloads still work because their vault-resolved node
    ``token`` is part of the config.
    """
    token = str((config or {}).get("token") or "").strip()
    return token or None
