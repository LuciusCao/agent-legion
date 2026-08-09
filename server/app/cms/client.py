from dataclasses import dataclass
from typing import Any

import requests

from server.app.cms.env import resolve_cms_env


class CmsClientError(RuntimeError):
    pass


def require_api_url(api_url: str | None, resource: str) -> str:
    """Return the configured CMS URL or fail with migration guidance.

    There is no built-in fallback host: the URL must come from the workspace
    node config (Settings UI), env ``CMS_BASE_URL``, or an ``api_url`` bound
    in the node config.
    """
    url = str(api_url or "").strip()
    if url:
        return url
    raise CmsClientError(
        f"CMS {resource} URL is not configured: set base_url/api_url in the "
        "workspace node config (Settings), or set env CMS_BASE_URL"
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
    from server.app.cms.auth import _generate_prod_token

    config = config or {}
    token = config.get("token")
    # A workspace-bound token (resolve_cms_resource marks it via
    # token_from_binding) wins over the env-level global default.
    if isinstance(token, str) and token and config.get("token_from_binding"):
        return str(token)
    env_token = resolve_cms_env("CMS_TOKEN")
    if env_token:
        return env_token
    if token:
        return str(token)
    if env == "prod":
        return _generate_prod_token(config)
    return None
