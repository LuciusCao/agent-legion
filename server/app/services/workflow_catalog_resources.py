from typing import Any
from urllib.parse import urlparse

from server.app.cms.auth import cms_token_available
from server.app.settings import Settings
from server.app.workflows.resource_schemas import resource_param_keys


def build_resource_providers(settings: Settings) -> list[dict[str, Any]]:
    providers_config = settings.config.get("resource_providers")
    if not isinstance(providers_config, dict):
        return []
    cms_config = settings.config.get("cms", {}) or {}
    declarations = settings.resource_providers

    result: list[dict[str, Any]] = []
    for key, meta in declarations.providers.items():
        provider = str(meta.get("provider") or "")
        provider_config = providers_config.get(provider) or {}
        path = str(provider_config.get("path", ""))
        param_keys = list(resource_param_keys(key, declarations.schemas))
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
                "config_schema": declarations.schemas.get(key, {}),
            }
        )
    return result


def build_global_services(settings: Settings) -> dict[str, Any]:
    cms_config = settings.config.get("cms", {}) or {}
    base_url = str(cms_config.get("base_url", ""))
    return {
        "cms": {
            "baseUrl": _mask_url(base_url) if base_url else "",
            "tokenConfigured": cms_token_available(cms_config),
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
