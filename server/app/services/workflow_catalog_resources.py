import os
from typing import Any
from urllib.parse import urlparse

from server.app.settings import Settings
from server.app.workflows.resource_schemas import (
    RESOURCE_PROVIDER_SCHEMAS,
    resource_param_keys,
)
from server.app.workflows.resources import RESOURCE_PROVIDERS


def build_resource_providers(settings: Settings) -> list[dict[str, Any]]:
    providers_config = settings.config.get("resource_providers")
    if not isinstance(providers_config, dict):
        return []
    cms_config = settings.config.get("cms", {}) or {}

    result: list[dict[str, Any]] = []
    for key, meta in RESOURCE_PROVIDERS.items():
        provider = str(meta.get("provider") or "")
        provider_config = providers_config.get(provider) or {}
        path = str(provider_config.get("path", ""))
        param_keys = list(resource_param_keys(key))
        default_params: dict[str, str] = {}
        for param_key in param_keys:
            if param_key in cms_config and cms_config[param_key] not in (None, ""):
                default_params[param_key] = str(cms_config[param_key])
        result.append(
            {
                "key": key,
                "provider": provider,
                "path": path,
                "defaultParams": default_params,
                "paramKeys": param_keys,
                "config_schema": RESOURCE_PROVIDER_SCHEMAS[key],
            }
        )
    return result


def build_global_services(settings: Settings) -> dict[str, Any]:
    cms_config = settings.config.get("cms", {}) or {}
    base_url = str(cms_config.get("base_url", ""))
    return {
        "cms": {
            "baseUrl": _mask_url(base_url) if base_url else "",
            "tokenConfigured": _token_available(cms_config),
            "env": str(cms_config.get("env", "")),
            "healthy": None,
            "lastCheckedAt": None,
        }
    }


def _mask_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    parts = hostname.split(".")
    if len(parts) >= 3:
        masked = f"{parts[0]}.***.{parts[-1]}"
    elif len(parts) == 2:
        masked = f"***.{parts[1]}"
    else:
        masked = hostname
    return f"{parsed.scheme}://{masked}{parsed.path}"


def _token_available(cms_config: dict[str, Any]) -> bool:
    if cms_config.get("token"):
        return True
    if os.environ.get("BASECMS_TOKEN"):
        return True
    token_gen = cms_config.get("token_gen") or {}
    if all(token_gen.get(k) for k in ("app_id", "nonce", "secret", "url")):
        return True
    return all(
        os.environ.get(env_key)
        for env_key in ("BASECMS_APP_ID", "BASECMS_NONCE", "BASECMS_SECRET", "BASECMS_TOKEN_URL")
    )
