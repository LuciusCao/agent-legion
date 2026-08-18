"""Service-config merge machinery behind ``NodeContext.service_config``.

Private implementation module (split from ``node_sdk`` for the size budget);
the public API is the ``NodeContext.service_config`` method. Standard library
only, no ``server.app.*`` imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Keys in node_config that reference (or carry) the injected connection; they
# are selectors, not business overrides, and never merge into service config.
CONNECTION_SELECTOR_KEYS = ("connection", "connection_config")


def merge_service_config(
    runtime: Mapping[str, Any],
    node_config: Mapping[str, Any],
    section: str | None,
    legacy_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Effective config for an external service the node talks to.

    Merge order (later wins): optional ``settings_config[section]`` base
    (machine/env-injected values), the dispatch-injected ``connection_config``
    block (resolved endpoint config + plaintext token, in memory only), then
    node/workspace business overrides. Empty values (``None``/``""``) never
    override; *legacy_keys* are retired pre-connection credential keys that
    only apply when no connection was injected (legacy frozen payloads).
    """
    merged: dict[str, Any] = {}
    if section is not None:
        settings_config = runtime.get("settings_config")
        if isinstance(settings_config, Mapping):
            base = settings_config.get(section)
            if isinstance(base, Mapping):
                merged.update(base)
    injected = node_config.get("connection_config")
    has_connection = isinstance(injected, Mapping) and bool(injected)
    if isinstance(injected, Mapping) and injected:
        merged.update({key: value for key, value in injected.items() if value not in (None, "")})
    for key, value in node_config.items():
        if key in CONNECTION_SELECTOR_KEYS or value in (None, ""):
            continue
        if has_connection and key in legacy_keys:
            continue
        merged[key] = value
    return merged
