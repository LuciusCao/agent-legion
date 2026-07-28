from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from server.app.workflows.resource_providers import RESOURCE_PROVIDERS
from server.app.workflows.resource_schemas import resource_param_keys


def _merge_resource_config(
    base: dict[str, Any],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(override, dict):
        return base
    result = {
        **base,
        "resources": dict(base.get("resources", {}))
        if isinstance(base.get("resources"), dict)
        else {},
    }
    override_resources = override.get("resources")
    if isinstance(override_resources, dict):
        for key, value in override_resources.items():
            result["resources"][key] = value
    return result


def _append_resource_params(
    api_url: str,
    config: dict[str, Any],
    param_keys: tuple[str, ...],
) -> str:
    parsed = urlparse(api_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in param_keys:
        value = config.get(key)
        if value not in (None, ""):
            query[key] = str(value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _provider_defaults(settings_config: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = settings_config.get("resource_providers")
    if not isinstance(providers, dict):
        return {}
    defaults = providers.get(provider)
    if not isinstance(defaults, dict):
        return {}
    result = dict(defaults)
    cms_config = settings_config.get("cms", {}) or {}
    base_url = str(cms_config.get("base_url", "")).rstrip("/")
    path = str(result.get("path", "")).lstrip("/")
    if base_url and path:
        result["api_url"] = f"{base_url}/{path}"
    return result


def resolve_cms_resource(
    settings_config: dict[str, Any],
    workspace: dict[str, Any] | None,
    batch_payload: dict[str, Any] | None,
    resource_key: str,
    node_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cms_config = settings_config.get("cms", {}) if isinstance(settings_config, dict) else {}
    result = dict(cms_config) if isinstance(cms_config, dict) else {}
    defaults = RESOURCE_PROVIDERS.get(resource_key, {})
    provider = str(defaults.get("provider") or "")
    url_key = str(defaults.get("url_key") or "")

    resource_config: dict[str, Any] = {}
    if workspace:
        workspace_resource_config = workspace.get("resource_config")
        if isinstance(workspace_resource_config, dict):
            resource_config = _merge_resource_config(resource_config, workspace_resource_config)
    if batch_payload:
        batch_resource_config = batch_payload.get("resource_config")
        if isinstance(batch_resource_config, dict):
            resource_config = _merge_resource_config(resource_config, batch_resource_config)

    binding = {}
    resources = resource_config.get("resources")
    if isinstance(resources, dict) and isinstance(resources.get(resource_key), dict):
        binding = resources[resource_key]
    # If new format explicitly sets enabled=false, don't use this binding
    if binding.get("enabled") is False:
        binding = {}
        provider = ""
        result.pop("api_url", None)
        if url_key:
            result.pop(url_key, None)
    binding_provider = binding.get("provider")
    if binding_provider:
        provider = str(binding_provider)
    result.update(_provider_defaults(settings_config, provider))
    raw_binding_config = binding.get("config")
    binding_config: dict[str, Any] = (
        dict(raw_binding_config) if isinstance(raw_binding_config, dict) else {}
    )
    result.update(binding_config)
    if node_config:
        # Executor node config (spec D15) wins over bindings and defaults for
        # the non-secret parameters its capability config_schema declares.
        result.update({key: value for key, value in node_config.items() if value not in (None, "")})
    # Explicit legacy URLs (settings-level question_*_url) win over the URL
    # derived from resource_providers base_url + path.
    api_url = str(
        binding_config.get("api_url") or result.get(url_key) or result.get("api_url") or ""
    )
    if api_url:
        # Params come from the merged result so global cms defaults (e.g.
        # bank_version) reach the URL even without a workspace binding.
        api_url = _append_resource_params(api_url, result, resource_param_keys(resource_key))
        result["api_url"] = api_url
        if url_key:
            result[url_key] = api_url
    if provider:
        result["provider"] = provider
    return result
