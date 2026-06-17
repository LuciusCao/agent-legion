import logging
import os
from typing import Any
from urllib.parse import urlparse

from server.app.services.job_errors import NotFoundError
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.registry import list_registered_workflows, load_registered_workflow
from server.app.workflows.resources import RESOURCE_PARAM_KEYS, RESOURCE_PROVIDERS

logger = logging.getLogger(__name__)


class PipelineCatalogService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def definition(self, pipeline_key: str) -> WorkflowDefinition:
        try:
            return load_registered_workflow(self.settings.root_dir, pipeline_key)
        except KeyError as exc:
            raise NotFoundError("Unknown pipeline") from exc

    def list_pipelines(self) -> list[dict[str, Any]]:
        pipelines: list[dict[str, Any]] = []
        for definition in list_registered_workflows(self.settings.root_dir):
            pipelines.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                }
            )
        return pipelines

    def pipeline(self, pipeline_key: str) -> dict[str, Any]:
        definition = self.definition(pipeline_key)
        nodes: list[dict[str, Any]] = []
        for node in definition.nodes.values():
            nodes.append(
                {
                    "key": node.key,
                    "label": node.label,
                    "capability": node.capability,
                    "after": node.after,
                    "inputs": node.inputs,
                    "outputs": node.outputs,
                }
            )
        return {
            "key": definition.key,
            "label": definition.label,
            "intake": {
                "modes": [
                    {
                        "key": mode.key,
                        "label": mode.label,
                        "input_field": mode.input_field,
                        "resource": mode.resource,
                    }
                    for mode in definition.intake.modes.values()
                ]
            },
            "nodes": nodes,
        }

    def resource_providers(self) -> list[dict[str, Any]]:
        providers_config = self.settings.config.get("resource_providers")
        if not isinstance(providers_config, dict):
            return []
        cms_config = self.settings.config.get("cms", {}) or {}

        result: list[dict[str, Any]] = []
        for key, meta in RESOURCE_PROVIDERS.items():
            provider = str(meta.get("provider") or "")
            provider_config = providers_config.get(provider) or {}
            path = str(provider_config.get("path", ""))
            param_keys = list(RESOURCE_PARAM_KEYS)
            if key == "question_detail" and "page_size" in param_keys:
                param_keys.remove("page_size")
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
                }
            )
        return result

    def global_services(self) -> dict[str, Any]:
        cms_config = self.settings.config.get("cms", {}) or {}
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
