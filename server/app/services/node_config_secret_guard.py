"""Intake-side guard for secret fields on non-vault channels (#432).

Split from ``node_secrets`` (at its budget ceiling): that module owns the
settings PATCH's vault diversion; this one owns the fail-fast gates for
channels with NO vault diversion — draft YAML ``node.config`` values and
``secret`` property defaults in the config_schema itself (VAULT-SECRET-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.config_schema import ConfigSchemaError
from server.app.services.node_secrets import is_secret_ref_marker, secret_config_fields

SECRET_FIELD_GUIDE = (
    "Secret values cannot travel through the workflow YAML source editor:"
    " they would land as plaintext in the revision and the intake freeze,"
    " bypassing the vault (VAULT-SECRET-001). Configure the field in the"
    " workspace's runtime-override channel (settings nodeConfig PATCH),"
    " which stores only a vault secret_ref marker."
)

SECRET_DEFAULT_GUIDE = (
    "A secret property cannot declare a default: the default is a plaintext"
    " credential that the schema-default merge would freeze verbatim into"
    " every intake snapshot (VAULT-SECRET-001, codex P1 on #432). Remove it"
    " and set the value via the runtime-override channel (settings nodeConfig"
    " PATCH), which diverts it into the vault."
)

_SECRET_DEFAULT_ERROR = "a secret property cannot declare a default"


def reject_secret_violations(
    config_schema: dict[str, Any],
    node_config: Mapping[str, Any],
    path: str,
) -> None:
    """Both #432 gates: secret field values in *node_config* must be the
    exact vault marker (or absent), and no ``secret: true`` property may
    declare a plaintext ``default`` (schema-error path ``{path}_schema``).
    The secret+default verdict is shared with ``validate_config_schema``
    (every declaration channel rejects it); this entry keeps the richer
    vault-channel error on the resolve/publish chains."""
    reject_secret_schema_defaults(config_schema, f"{path}_schema")
    for field in secret_config_fields(config_schema):
        if field not in node_config:
            continue
        value = node_config[field]
        if is_secret_ref_marker(value) and str(value["secret_ref"]).startswith("node:"):
            continue
        raise ConfigSchemaError(f"{path}.{field}: {SECRET_FIELD_GUIDE}")


def reject_secret_schema_defaults(config_schema: dict[str, Any], path: str) -> None:
    """Raise when a ``secret: true`` property also declares a ``default``:
    a plaintext schema default flows into the effective config via
    ``config_schema_defaults`` even when the node config never sets the
    field — freezing the credential verbatim (codex P1 on #432). The
    ``validate_config_schema`` check rejects secret+default on every
    declaration channel (Agent definitions, node blocks, executors);
    this face adds the vault-channel pointer for the resolve/publish gates."""
    properties = config_schema.get("properties")
    if not isinstance(properties, dict):
        return
    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.get("secret") and "default" in prop:
            raise ConfigSchemaError(f"{path}.properties.{name}: {SECRET_DEFAULT_GUIDE}")


def secret_gate_errors(schemas: Mapping[str, dict[str, Any]], definition: Any) -> list[str]:
    """Both gates' verdicts per executable draft node, as publish-gate error
    strings: fail at publish instead of stranding the revision at intake."""
    errors: list[str] = []
    for node in definition.executable_nodes.values():
        if node.node_type == "approval" or not schemas.get(node.key):
            continue
        schema = schemas[node.key]
        try:
            reject_secret_schema_defaults(schema, f"nodes.{node.key}.config_schema")
        except ConfigSchemaError as exc:
            errors.append(str(exc))
        if not node.config:
            continue
        try:
            reject_secret_violations(schema, node.config, f"nodes.{node.key}.config")
        except ConfigSchemaError as exc:
            errors.append(str(exc))
    return errors
