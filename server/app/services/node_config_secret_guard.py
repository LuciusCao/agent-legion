"""Intake-side guard for secret fields on non-vault channels (#432).

Split from ``node_secrets`` (at its budget ceiling): that module owns the
settings PATCH's vault diversion; this one owns the fail-fast gate for
channels that have NO vault diversion — the draft YAML ``node.config``.
``resolve_node_config``'s passthrough loop used to wave secret field values
through into the frozen intake config unvalidated: the ``{"secret_set":
true}`` settings-echo shape froze as dead config, a plaintext string froze
as plaintext into the revision and every job snapshot (VAULT-SECRET-001).
The only accepted shape is the exact vault marker the settings PATCH
stores; everything else is rejected with a pointer to the vault-backed
channel. The error names the field but never echoes the value.
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


def reject_node_secret_fields(
    config_schema: dict[str, Any],
    node_config: Mapping[str, Any],
    path: str,
) -> None:
    """Raise ``ConfigSchemaError`` unless every secret field value in
    *node_config* is the exact ``{"secret_ref": "node:..."}`` vault marker
    (or absent). Called by ``resolve_node_config`` (intake/dispatch/upgrade
    freeze chains) and the draft publish gate — the latter surfaces the
    error in Studio before the revision can strand intake."""
    for field in secret_config_fields(config_schema):
        if field not in node_config:
            continue
        value = node_config[field]
        if is_secret_ref_marker(value) and str(value["secret_ref"]).startswith("node:"):
            continue
        raise ConfigSchemaError(f"{path}.{field}: {SECRET_FIELD_GUIDE}")


def secret_gate_errors(
    schemas: Mapping[str, dict[str, Any]],
    definition: Any,
) -> list[str]:
    """``reject_node_secret_fields`` verdicts for every executable node of a
    draft, as publish-gate error strings (#432). Fail-fast at publish: the
    YAML source editor has no vault channel, and surfacing the error only
    at the first job's intake would strand a published revision no new job
    can use."""
    errors: list[str] = []
    for node in definition.executable_nodes.values():
        if node.node_type == "approval":
            continue
        schema = schemas.get(node.key)
        if schema and node.config:
            try:
                reject_node_secret_fields(schema, node.config, f"nodes.{node.key}.config")
            except ConfigSchemaError as exc:
                errors.append(str(exc))
    return errors
