"""Secret-safe config handling for kind='code' manifests.

Split from ``code_dispatch.py`` when the #389 shard changes outgrew the
parent's size budget. These two functions are the enqueue/claim halves of
the CONFIG-MANIFEST-001 contract: the queued manifest carries only
non-secret keys plus vault ``secret_ref`` markers, and the claim response
resolves the plaintext in memory only.
"""

from __future__ import annotations

from typing import Any

from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.vault import VaultService


class PlaintextSecretError(ValueError):
    """A secret-marked config key holds a legacy plaintext value.

    Plaintext secrets can never be persisted in the queued manifest
    (VAULT-SECRET-001), so the node is not Worker-routable; the caller falls
    back to local execution, which resolves secrets in memory only.
    """


def split_manifest_config(
    schema: dict[str, Any], unresolved_config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split an UNRESOLVED node config into persistable parts.

    Returns ``(config, secret_config)``: non-secret schema-whitelisted keys
    (CONFIG-MANIFEST-001) go to ``config`` verbatim; secret-marked keys go to
    ``secret_config`` only in vault ``{"secret_ref": name}`` form (a
    reference, not a secret). A legacy plaintext secret value raises
    ``PlaintextSecretError``.
    """
    raw_properties = schema.get("properties") if isinstance(schema, dict) else None
    properties = raw_properties if isinstance(raw_properties, dict) else {}
    config: dict[str, Any] = {}
    secret_config: dict[str, Any] = {}
    for key, value in unresolved_config.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        if not prop.get("secret", False):
            config[key] = value
            continue
        if value in (None, ""):
            continue
        if isinstance(value, dict) and "secret_ref" in value:
            secret_config[key] = value
            continue
        raise PlaintextSecretError(
            f"secret config key {key!r} holds a legacy plaintext value; "
            "the node stays on local execution"
        )
    return config, secret_config


def resolve_code_manifest_config(
    manifest: dict[str, Any],
    database_dsn: Any,
    settings_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Claim-time secret injection for a claimed kind='code' manifest.

    Returns a manifest copy whose ``config`` is fully resolved (vault
    plaintext + the injected connection block), with ``secret_config``
    removed. Runs on the claim-response path only, after the claim
    transaction committed: the resolved plaintext crosses the existing HTTPS
    channel to the Worker and is never persisted.
    """
    config = {**manifest.get("config", {}), **manifest.get("secret_config", {})}
    schema = manifest.get("config_schema") or {}
    vault = VaultService(database_dsn, settings_config)
    config = vault.resolve_secret_refs(config, str(manifest.get("workspace_id") or ""))
    config = inject_connection_config(
        config, schema, ConnectionTokenService(database_dsn, settings_config)
    )
    resolved = {**manifest, "config": config}
    resolved.pop("secret_config", None)
    return resolved
