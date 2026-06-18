from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

RESOURCE_PROVIDERS = {
    "question_detail": {
        "provider": "cms.question.detail",
        "url_key": "question_detail_url",
    },
    "by_knowledge": {
        "provider": "cms.question.list_by_knowledge",
        "url_key": "question_list_url",
    },
}

RESOURCE_PARAM_KEYS = ("bank_version", "country_id", "subject_id", "page_size")


def _resource_config_from_legacy_cms(cms_config: dict[str, Any]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    if cms_config.get("question_detail_url"):
        resources["question_detail"] = {
            "provider": "cms.question.detail",
            "config": {
                "api_url": cms_config["question_detail_url"],
                **{
                    key: cms_config[key]
                    for key in ("bank_version", "country_id", "subject_id")
                    if key in cms_config
                },
            },
        }
    if cms_config.get("question_list_url"):
        resources["by_knowledge"] = {
            "provider": "cms.question.list_by_knowledge",
            "config": {
                "api_url": cms_config["question_list_url"],
                **{
                    key: cms_config[key]
                    for key in ("bank_version", "country_id", "subject_id", "page_size")
                    if key in cms_config
                },
            },
        }
    return {"resources": resources} if resources else {}


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


def _append_resource_params(api_url: str, config: dict[str, Any]) -> str:
    parsed = urlparse(api_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in RESOURCE_PARAM_KEYS:
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
) -> dict[str, Any]:
    cms_config = settings_config.get("cms", {}) if isinstance(settings_config, dict) else {}
    result = dict(cms_config) if isinstance(cms_config, dict) else {}
    defaults = RESOURCE_PROVIDERS.get(resource_key, {})
    provider = str(defaults.get("provider") or "")
    url_key = str(defaults.get("url_key") or "")

    resource_config: dict[str, Any] = {}
    if isinstance(cms_config, dict):
        resource_config = _merge_resource_config(
            resource_config,
            _resource_config_from_legacy_cms(cms_config),
        )
    if workspace:
        workspace_resource_config = workspace.get("resource_config")
        workspace_cms_config = workspace.get("cms_config")
        if isinstance(workspace_cms_config, dict):
            resource_config = _merge_resource_config(
                resource_config,
                _resource_config_from_legacy_cms(workspace_cms_config),
            )
            result.update(workspace_cms_config)
        if isinstance(workspace_resource_config, dict):
            resource_config = _merge_resource_config(resource_config, workspace_resource_config)
    if batch_payload:
        batch_cms_config = batch_payload.get("cms_config")
        batch_resource_config = batch_payload.get("resource_config")
        if isinstance(batch_cms_config, dict):
            resource_config = _merge_resource_config(
                resource_config,
                _resource_config_from_legacy_cms(batch_cms_config),
            )
            result.update(batch_cms_config)
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
    api_url = str(
        binding_config.get("api_url") or result.get("api_url") or result.get(url_key) or ""
    )
    if api_url:
        api_url = _append_resource_params(api_url, binding_config)
        result["api_url"] = api_url
        if url_key:
            result[url_key] = api_url
    if provider:
        result["provider"] = provider
    return result
