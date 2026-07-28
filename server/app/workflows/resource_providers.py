"""Resource provider declarations loaded from config yaml (spec D11).

Providers are declared in the ``resource_providers:`` section of the
application config. Alongside the runtime URL defaults (``path``), each
provider declares which resource key it serves (``resource_key``), the legacy
settings URL key (``url_key``) and the typed ``config_schema`` for its tunable
parameters. Declarations are validated at ``load_settings`` time and injected
through ``Settings.resource_providers``; invalid declarations fail startup,
mirroring Agent Definition loading. This module intentionally performs no I/O
at import time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from server.app.config_schema import ConfigSchemaError, validate_config_schema
from server.app.workflows.schema import WorkflowDefinitionError


@dataclass(frozen=True)
class ResourceProviderDeclarations:
    """Validated resource provider declarations keyed by resource key."""

    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_resource_provider_declarations(raw: Any) -> ResourceProviderDeclarations:
    """Parse and validate the yaml ``resource_providers:`` section."""
    if raw is None:
        return ResourceProviderDeclarations()
    if not isinstance(raw, dict):
        raise ConfigSchemaError("resource_providers must be a mapping")
    providers: dict[str, dict[str, Any]] = {}
    schemas: dict[str, dict[str, Any]] = {}
    for provider_name, entry in raw.items():
        if not isinstance(provider_name, str) or not provider_name:
            raise ConfigSchemaError("resource_providers keys must be non-empty strings")
        path = f"resource_providers.{provider_name}"
        if not isinstance(entry, dict):
            raise ConfigSchemaError(f"{path} must be a mapping")
        resource_key = entry.get("resource_key")
        if not isinstance(resource_key, str) or not resource_key:
            raise ConfigSchemaError(f"{path}.resource_key must be a non-empty string")
        if resource_key in providers:
            raise ConfigSchemaError(
                f"resource key {resource_key!r} is declared by multiple providers"
            )
        url_key = entry.get("url_key", "")
        if not isinstance(url_key, str):
            raise ConfigSchemaError(f"{path}.url_key must be a string")
        schema = entry.get("config_schema") or {}
        validate_config_schema(schema, path=f"{path}.config_schema")
        providers[resource_key] = {"provider": provider_name, "url_key": url_key}
        schemas[resource_key] = schema
    return ResourceProviderDeclarations(providers=providers, schemas=schemas)


def validate_node_resource_references(
    node_key: str,
    resources: list[str],
    providers: Mapping[str, Any] | None = None,
) -> None:
    """Fail workflow loading when a node references an unknown resource key.

    ``providers`` is the validated declaration mapping (resource key → provider
    metadata) injected from ``Settings``. ``None`` skips the check; it is used
    only when re-loading persisted definition snapshots that were validated at
    intake/publish time.
    """
    if providers is None:
        return
    for resource_key in resources:
        if resource_key not in providers:
            raise WorkflowDefinitionError(
                f"Node {node_key} references unknown resource {resource_key!r}"
            )
